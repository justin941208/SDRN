import numpy as np
from skimage import io, transform
import os
import sys
import matplotlib.pyplot as plt
import math
import time
import argparse
import ast
import scipy.io as sio
import copy
from visualize import show, showMesh, showImage, showLandmark, showLandmark2
import pickle
from dataloader import ImageData
from torchmodel import TorchNet
from dataloader import getDataLoader, DataGenerator
from loss import getErrorFunction, getLossFunction
import masks
from data import getColors
import torch
from torch.utils.tensorboard import SummaryWriter
from buildblocks import data_block_names, NUM_BLOCKS

now_time = time.localtime()
save_dir_time = '/' + str(now_time.tm_year) + '-' + str(now_time.tm_mon) + '-' + str(now_time.tm_mday) + '-' \
                + str(now_time.tm_hour) + '-' + str(now_time.tm_min) + '-' + str(now_time.tm_sec)
writer = SummaryWriter(log_dir='tmp' + save_dir_time)


class NetworkManager:
    def __init__(self, args):
        self.train_data = []
        self.val_data = []
        self.test_data = []

        self.gpu_num = args.gpu
        self.num_worker = args.numWorker
        self.batch_size = args.batchSize

        self.model_save_path = args.modelSavePath + save_dir_time
        if not os.path.exists(args.modelSavePath):
            os.mkdir(args.modelSavePath)
        if not os.path.exists(self.model_save_path):
            os.mkdir(self.model_save_path)

        self.epoch = args.epoch
        self.start_epoch = args.startEpoch

        self.error_function = args.errorFunction

        self.net = TorchNet(gpu_num=args.gpu, visible_gpus=args.visibleDevice, learning_rate=args.learningRate)  # class of
        # RZYNet
        # if true, provide [pos offset R T] as groundtruth. Otherwise ,provide pos as GT

        self.is_pre_read = args.isPreRead

        # 0: normal PRN [image posmap]  1: offset [image offset R T S]
        self.weight_decay = 0.0001

        self.criterion = None
        self.metrics = None
        # id   model_builder  data_loader_mode   #of metrics number   #of getitem elem
        self.mode_dict = {'InitPRN': [0, self.net.buildInitPRN, 'posmap', 1, 1],
                          'OffsetPRN': [1, self.net.buildOffsetPRN, 'offset', 5, 5],
                          'AttentionPRN': [2, self.net.buildAttentionPRN, 'attention', 2, 2],
                          'QuaternionOffsetPRN': [3, self.net.buildQuaternionOffsetPRN, 'quaternionoffset', 4, 4],
                          'SiamPRN': [4, self.net.buildSiamPRN, 'siam', 3, 2],
                          'MeanOffsetPRN': [3, self.net.buildMeanOffsetPRN, 'meanoffset', 4, 4]}
        self.mode = self.mode_dict['InitPRN']

    def buildModel(self, args):
        print('building', args.netStructure)
        if args.netStructure in self.mode_dict.keys():
            self.mode = self.mode_dict[args.netStructure]
            self.mode[1]()
        else:
            print('unknown network structure')

    def addImageData(self, data_dir, add_mode='train', split_rate=0.8):
        all_data = []
        for root, dirs, files in os.walk(data_dir):
            for dir_name in dirs:
                image_name = dir_name
                if not os.path.exists(root + '/' + dir_name + '/' + image_name + '_cropped.jpg'):
                    print('skip ', root + '/' + dir_name)
                    continue
                temp_image_data = ImageData()
                temp_image_data.readPath(root + '/' + dir_name)
                all_data.append(temp_image_data)
        print(len(all_data), 'data added')

        if add_mode == 'train':
            self.train_data.extend(all_data)
        elif add_mode == 'val':
            self.val_data.extend(all_data)
        elif add_mode == 'both':
            num_train = math.floor(len(all_data) * split_rate)
            self.train_data.extend(all_data[0:num_train])
            self.val_data.extend(all_data[num_train:])
        elif add_mode == 'test':
            self.test_data.extend(all_data)

    def saveImageDataPaths(self, save_folder='data'):
        print('saving data path list')
        ft = open(save_folder + '/' + 'train_data.pkl', 'wb')
        fv = open(save_folder + '/' + 'val_data.pkl', 'wb')
        pickle.dump(self.train_data, ft)
        pickle.dump(self.val_data, fv)
        ft.close()
        fv.close()
        print('data path list saved')

    def loadImageDataPaths(self, load_folder='data'):
        print('loading data path list')
        ft = open(load_folder + '/' + 'train_data.pkl', 'rb')
        fv = open(load_folder + '/' + 'val_data.pkl', 'rb')
        self.train_data = pickle.load(ft)
        self.val_data = pickle.load(fv)
        ft.close()
        fv.close()
        print('data path list loaded')

    def train(self):
        best_acc = 1000
        model = self.net.model
        optimizer = self.net.optimizer
        scheduler = self.net.scheduler

        val_data_loader = getDataLoader(self.val_data, mode=self.mode[2], batch_size=self.batch_size * self.gpu_num, is_shuffle=False, is_aug=False,
                                        is_pre_read=True, num_worker=0)

        for epoch in range(self.start_epoch, self.epoch):
            print('Epoch: %d' % epoch)
            scheduler.step()
            model.train()

            sum_loss = 0.0
            t_start = time.time()
            num_output = self.mode[3]
            num_input = self.mode[4]
            sum_metric_loss = np.zeros(num_output)

            num_fed_batch = 0
            for block_id in range(NUM_BLOCKS):
                print('\rloading data path list', end='')
                ft = open(data_block_names[block_id], 'rb')
                self.train_data = pickle.load(ft)
                ft.close()
                print('\rdata path list loaded', end='')
                train_data_loader = getDataLoader(self.train_data, mode=self.mode[2], batch_size=self.batch_size * self.gpu_num, is_shuffle=True, is_aug=True,
                                                  is_pre_read=False, num_worker=self.num_worker)
                total_itr_num = len(train_data_loader.dataset) // train_data_loader.batch_size
                for i, data in enumerate(train_data_loader):
                    num_fed_batch += 1
                    # 准备数据
                    x = data[0]
                    x = x.to(self.net.device).float()
                    y = [data[j] for j in range(1, 1 + num_input)]
                    for j in range(num_input):
                        y[j] = y[j].to(x.device).float()
                    optimizer.zero_grad()
                    outputs = model(x, *y)

                    loss = torch.mean(outputs[0])
                    metrics_loss = [torch.mean(outputs[j]) for j in range(1, 1 + num_output)]
                    loss.backward()
                    optimizer.step()
                    sum_loss += loss.item()
                    print('\r', end='')
                    print('[epoch:%d, block:%d/%d, iter:%d/%d, time:%d] Loss: %.04f ' % (epoch + 1, block_id + 1, NUM_BLOCKS, i + 1, total_itr_num,
                                                                                         int(time.time() - t_start), sum_loss / (num_fed_batch + 1)), end='')
                    for j in range(num_output):
                        sum_metric_loss[j] += metrics_loss[j]
                        print(' Metrics%d: %.04f ' % (j, sum_metric_loss[j] / (num_fed_batch + 1)), end='')

            # validation

            with torch.no_grad():
                val_sum_metric_loss = np.zeros(self.mode[3])
                model.eval()
                val_i = 0
                print("\nWaiting Test!", val_i, end='\r')
                for i, data in enumerate(val_data_loader):
                    val_i += 1
                    print("Waiting Test!", val_i, end='\r')
                    x = data[0]
                    x = x.to(self.net.device).float()
                    y = [data[j] for j in range(1, 1 + num_input)]
                    for j in range(num_input):
                        y[j] = y[j].to(x.device).float()
                    outputs = model(x, *y)
                    metrics_loss = [torch.mean(outputs[j]) for j in range(1, 1 + num_output)]
                    for j in range(num_output):
                        val_sum_metric_loss[j] += metrics_loss[j]

                for j in range(num_output):
                    print('val Metrics%d: %.04f ' % (j, val_sum_metric_loss[j] / len(val_data_loader)), end='')
                val_loss = val_sum_metric_loss[0]

                print('\nSaving model......', end='\r')
                if self.gpu_num > 1:
                    torch.save(model.module.state_dict(), '%s/net_%03d.pth' % (self.model_save_path, epoch + 1))
                else:
                    torch.save(model.state_dict(), '%s/net_%03d.pth' % (self.model_save_path, epoch + 1))
                # save best
                if val_loss / len(val_data_loader) < best_acc:
                    print('new best %.4f improved from %.4f' % (val_loss / len(val_data_loader), best_acc))
                    best_acc = val_loss / len(val_data_loader)
                    if self.gpu_num > 1:
                        torch.save(model.module.state_dict(), '%s/best.pth' % self.model_save_path)
                    else:
                        torch.save(model.state_dict(), '%s/best.pth' % self.model_save_path)
                else:
                    print('not improved from %.4f' % best_acc)

            # write log

            writer.add_scalar('train/loss', sum_loss / len(train_data_loader), epoch + 1)
            for j in range(self.mode[3]):
                writer.add_scalar('train/metrics%d' % j, sum_metric_loss[j] / len(train_data_loader), epoch + 1)
                writer.add_scalar('val/metrics%d' % j, val_sum_metric_loss[j] / len(val_data_loader), epoch + 1)

    def test(self, error_func_list=None, is_visualize=False):
        total_task = len(self.test_data)
        print('total img:', total_task)

        model = self.net.model
        total_error_list = []
        num_output = self.mode[3]
        num_input = self.mode[4]
        data_generator = DataGenerator(all_image_data=self.test_data, mode=self.mode[2], is_aug=False, is_pre_read=self.is_pre_read)

        with torch.no_grad():
            model.eval()
            for i in range(len(self.test_data)):
                data = data_generator.__getitem__(i)
                x = data[0]
                x = x.to(self.net.device).float()
                y = [data[j] for j in range(1, 1 + num_input)]
                for j in range(num_input):
                    y[j] = y[j].to(x.device).float()
                    y[j] = torch.unsqueeze(y[j], 0)
                x = torch.unsqueeze(x, 0)

                # if self.mode[0] == 1:
                #     outputs = model(x, y[0], y[1], y[2], y[3], y[4])
                # elif self.mode[0] == 2:
                #     outputs = model(x, y[0], y[1])
                # elif self.mode[0] == 3:
                #     outputs = model(x, y[0], y[1], y[2], y[3])
                # elif self.mode[0] == 4:
                #     outputs = model(x, y[0], y[1])
                # else:
                #     outputs = model(x, y[0])
                outputs = model(x, *y)

                p = outputs[-1]
                x = x.squeeze().cpu().numpy().transpose(1, 2, 0)
                p = p.squeeze().cpu().numpy().transpose(1, 2, 0) * 280
                b = sio.loadmat(self.test_data[i].bbox_info_path)
                gt_y = y[0]
                gt_y = gt_y.squeeze().cpu().numpy().transpose(1, 2, 0) * 280

                temp_errors = []
                for error_func_name in error_func_list:
                    error_func = getErrorFunction(error_func_name)
                    error = error_func(gt_y, p, b['Bbox'], b['Kpt'])
                    temp_errors.append(error)
                total_error_list.append(temp_errors)
                print(self.test_data[i].init_image_path, temp_errors)
                if is_visualize:
                    init_image = np.load(self.test_data[i].cropped_image_path).astype(np.float32) / 255.0
                    showImage(init_image)
                    diff = np.square(gt_y - p) * masks.face_mask_np3d
                    dist2d = np.sqrt(np.sum(diff[:, 0:2], axis=-1))
                    dist3d = np.sqrt(np.sum(diff[:, 0:3], axis=-1))
                    visibility = np.load(self.test_data[i].attention_mask_path.replace('attention', 'visibility')).astype(np.float32)

                    plt.subplot(2, 2, 1)
                    plt.imshow(init_image)
                    plt.subplot(2, 2, 2)
                    plt.imshow(dist2d)
                    plt.subplot(2, 2, 3)
                    plt.imshow(dist3d)
                    plt.subplot(2, 2, 4)
                    plt.imshow(visibility)
                    plt.show()

                    if temp_errors[0] > 0.06:
                        tex = np.load(self.test_data[i].texture_path).astype(np.float32)
                        init_image = np.load(self.test_data[i].cropped_image_path).astype(np.float32) / 255.0
                        show([p, tex, init_image], mode='uvmap')
                mean_errors = np.mean(total_error_list, axis=0)
            for i in range(len(error_func_list)):
                print(error_func_list[i], mean_errors[i])

            se_idx = np.argsort(np.sum(total_error_list, axis=-1))
            se_data_list = np.array(self.test_data)[se_idx]
            se_path_list = [a.cropped_image_path for a in se_data_list]
            sep = '\n'
            fout = open('errororder.txt', 'w', encoding='utf-8')
            fout.write(sep.join(se_path_list))
            fout.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='model arguments')

    parser.add_argument('--gpu', default=1, type=int, help='gpu number')
    parser.add_argument('--batchSize', default=16, type=int, help='batchsize')
    parser.add_argument('--epoch', default=30, type=int, help='epoch')
    parser.add_argument('--modelSavePath', default='savedmodel/temp_best_model', type=str, help='model save path')
    parser.add_argument('-td', '--trainDataDir', nargs='+', type=str, help='training image directories')
    parser.add_argument('-vd', '--valDataDir', nargs='+', type=str, help='validation image directories')
    parser.add_argument('-pd', '--testDataDir', nargs='+', type=str, help='test/predict image directories')
    parser.add_argument('--foreFaceMaskPath', default='uv-data/uv_face_mask.png', type=str, help='')
    parser.add_argument('--weightMaskPath', default='uv-data/uv_weight_mask.png', type=str, help='')
    parser.add_argument('--uvKptPath', default='uv-data/uv_kpt_ind.txt', type=str, help='')
    parser.add_argument('-train', '--isTrain', default=False, type=ast.literal_eval, help='')
    parser.add_argument('-test', '--isTest', default=False, type=ast.literal_eval, help='')
    parser.add_argument('-testsingle', '--isTestSingle', default=False, type=ast.literal_eval, help='')
    parser.add_argument('-visualize', '--isVisualize', default=False, type=ast.literal_eval, help='')
    parser.add_argument('--errorFunction', default='nme2d', nargs='+', type=str)
    parser.add_argument('--loadModelPath', default=None, type=str, help='')
    parser.add_argument('--visibleDevice', default='0', type=str, help='')
    parser.add_argument('-struct', '--netStructure', default='InitPRNet', type=str, help='')
    parser.add_argument('-lr', '--learningRate', default=1e-4, type=float)
    parser.add_argument('--startEpoch', default=0, type=int)
    parser.add_argument('--isPreRead', default=False, type=ast.literal_eval)
    parser.add_argument('--numWorker', default=4, type=int, help='loader worker number')

    run_args = parser.parse_args()

    print(run_args)

    os.environ["CUDA_VISIBLE_DEVICES"] = run_args.visibleDevice
    print(torch.cuda.is_available(), torch.cuda.device_count(), torch.cuda.current_device(), torch.cuda.get_device_name(0))

    net_manager = NetworkManager(run_args)
    net_manager.buildModel(run_args)
    if run_args.isTrain:
        for dir in run_args.valDataDir:
            net_manager.addImageData(dir, 'val')
        if run_args.loadModelPath is not None:
            net_manager.net.loadWeights(run_args.loadModelPath)
        net_manager.train()

    if run_args.isTest:
        for dir in run_args.testDataDir:
            net_manager.addImageData(dir, 'test')
        if run_args.loadModelPath is not None:
            net_manager.net.loadWeights(run_args.loadModelPath)
            net_manager.test(error_func_list=run_args.errorFunction, is_visualize=run_args.isVisualize)

    writer.close()