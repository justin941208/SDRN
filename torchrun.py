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
import random

now_time = time.localtime()
save_dir_time = '/' + str(now_time.tm_year) + '-' + str(now_time.tm_mon) + '-' + str(now_time.tm_mday) + '-' \
                + str(now_time.tm_hour) + '-' + str(now_time.tm_min) + '-' + str(now_time.tm_sec)
writer = SummaryWriter(log_dir='tmp2' + save_dir_time)


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
        # if not os.path.exists(self.model_save_path):
        #     os.mkdir(self.model_save_path)

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
                          'MeanOffsetPRN': [3, self.net.buildMeanOffsetPRN, 'meanoffset', 4, 4],
                          'VisiblePRN': [5, self.net.buildVisiblePRN, 'visible', 4, 3],
                          'SDRN': [5, self.net.buildSDRN, 'visible', 4, 3],
                          'PPRN': [5, self.net.buildPPRN, 'visible', 4, 3],
                          'SDRNv2': [5, self.net.buildSDRNv2, 'visible', 4, 3],
                          'FinetuneSDRN': [5, self.net.buildFinetuneSDRN, 'visible', 4, 3],
                          'FinetuneKPT': [5, self.net.buildFinetuneKPT, 'kpt3d', 4, 3],
                          'SRN': [5, self.net.buildSRN, 'visible', 4, 3],
                          'FinetunePPRN': [5, self.net.buildFinetunePPRN, 'visible', 4, 3],
                          'RefNet': [6, self.net.buildRefNet, 'visible', 5, 3]
                          }
        self.mode = self.mode_dict['InitPRN']
        self.net_structure = ''

    def buildModel(self, args):
        print('building', args.netStructure)
        if args.netStructure in self.mode_dict.keys():
            self.mode = self.mode_dict[args.netStructure]
            self.mode[1]()
            self.net_structure = args.netStructure
        else:
            print('unknown network structure')

    def addImageData(self, data_dir, add_mode='train', split_rate=0.8):
        all_data = []
        for root, dirs, files in os.walk(data_dir):
            dirs.sort()  # keep order in linux
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
        if not os.path.exists(self.model_save_path):
            os.mkdir(self.model_save_path)
        best_acc = 1000
        model = self.net.model
        optimizer = self.net.optimizer
        scheduler = self.net.scheduler

        # from thop import profile
        # sample_input = torch.randn((1, 3, 256, 256)).to(self.net.device)
        # sample_output = torch.randn((1, 3, 256, 256)).to(self.net.device)
        # flops, params = profile(model, inputs=(sample_input, sample_output))
        # print('params:%d  flops:%d' % (params, flops))

        # l2_weight_loss = torch.tensor(0).to(self.net.device).float()
        # for name, param in model.named_parameters():
        #     if 'weight' in name:
        #         l2_weight_loss += torch.norm(param, 2)
        train_data_loader = getDataLoader(self.train_data, mode=self.mode[2], batch_size=self.batch_size * self.gpu_num, is_shuffle=False, is_aug=True,
                                          is_pre_read=self.is_pre_read, num_worker=self.num_worker)
        val_data_loader = getDataLoader(self.val_data, mode=self.mode[2], batch_size=self.batch_size * self.gpu_num, is_shuffle=False, is_aug=False,
                                        is_pre_read=True, num_worker=0)

        for epoch in range(self.start_epoch, self.epoch):
            print('Epoch: %d' % epoch)
            scheduler.step()
            model.train()
            total_itr_num = len(train_data_loader.dataset) // train_data_loader.batch_size

            sum_loss = 0.0
            t_start = time.time()
            num_output = self.mode[3]
            num_input = self.mode[4]
            sum_metric_loss = np.zeros(num_output)

            for i, data in enumerate(train_data_loader):
                # 准备数据
                x = data[0]
                x = x.to(self.net.device).float()
                y = [data[j] for j in range(1, 1 + num_input)]
                for j in range(num_input):
                    y[j] = y[j].to(x.device).float()
                optimizer.zero_grad()
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

                loss = torch.mean(outputs[0])
                metrics_loss = [torch.mean(outputs[j]) for j in range(1, 1 + num_output)]
                loss.backward()
                optimizer.step()
                sum_loss += loss.item()
                print('\r', end='')
                print('[epoch:%d, iter:%d/%d, time:%d] Loss: %.04f ' % (epoch, i + 1, total_itr_num, int(time.time() - t_start), sum_loss / (i + 1)),
                      end='')
                for j in range(num_output):
                    sum_metric_loss[j] += metrics_loss[j]
                    print(' Metrics%d: %.04f ' % (j, sum_metric_loss[j] / (i + 1)), end='')

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
        from loss import cp, uv_kpt
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
                outputs = model(x, *y)

                p = outputs[-1]
                x = x.squeeze().cpu().numpy().transpose(1, 2, 0)
                p = p.squeeze().cpu().numpy().transpose(1, 2, 0) * 280
                b = sio.loadmat(self.test_data[i].bbox_info_path)
                gt_y = y[0]
                gt_y = gt_y.squeeze().cpu().numpy().transpose(1, 2, 0) * 280

                # for PRN GT
                # Tform = cp(p[uv_kpt[:, 0], uv_kpt[:, 1], :], gt_y[uv_kpt[:, 0], uv_kpt[:, 1], :])
                # p = p.dot(Tform[0:3, 0:3].T) + Tform[0:3, 3]

                temp_errors = []
                for error_func_name in error_func_list:
                    error_func = getErrorFunction(error_func_name)
                    error = error_func(gt_y, p, b['Bbox'], b['Kpt'])
                    temp_errors.append(error)
                total_error_list.append(temp_errors)
                print(self.test_data[i].init_image_path, end='  ')
                for er in temp_errors:
                    print('%.5f' % er, end=' ')
                print(i)
                if temp_errors[0] > 0.07:
                    print('failure')
                if is_visualize:
                    init_image = np.load(self.test_data[i].cropped_image_path).astype(np.float32) / 255.0
                    plt.axis('off')
                    plt.imshow(init_image)
                    plt.show()
                    if temp_errors[0] > 1.00:
                        init_image = np.load(self.test_data[i].cropped_image_path).astype(np.float32) / 255.0
                        diff = np.square(gt_y - p) * masks.face_mask_np3d
                        dist2d = np.sqrt(np.sum(diff[:, :, 0:2], axis=-1))
                        dist2d[0, 0] = 30.0
                        dist3d = np.sqrt(np.sum(diff[:, :, 0:3], axis=-1))
                        dist3d[0, 0] = 30.0
                        dist3 = np.sqrt(diff[:, :, 2])
                        dist3[0, 0] = 30.0
                        visibility = np.load(self.test_data[i].attention_mask_path.replace('attention', 'visibility')).astype(np.float32)

                        plt.subplot(2, 3, 1)
                        plt.imshow(init_image)
                        plt.subplot(2, 3, 2)
                        plt.imshow(dist2d)
                        plt.subplot(2, 3, 3)
                        plt.imshow(dist3d)
                        plt.subplot(2, 3, 4)
                        plt.imshow(dist3)
                        plt.subplot(2, 3, 5)
                        plt.imshow(visibility)
                        plt.show()

                        tex = np.load(self.test_data[i].texture_path.replace('zeroz2', 'full')).astype(np.float32)
                        init_image = np.load(self.test_data[i].cropped_image_path).astype(np.float32) / 255.0
                        show([p, tex, init_image], mode='uvmap')
                        init_image = np.load(self.test_data[i].cropped_image_path).astype(np.float32) / 255.0
                        show([gt_y, tex, init_image], mode='uvmap')
                mean_errors = np.mean(total_error_list, axis=0)
                for er in mean_errors:
                    print('%.5f' % er, end=' ')
                print('')
            for i in range(len(error_func_list)):
                print(error_func_list[i], mean_errors[i])

            se_idx = np.argsort(np.sum(total_error_list, axis=-1))
            se_data_list = np.array(self.test_data)[se_idx]
            se_path_list = [a.cropped_image_path for a in se_data_list]
            sep = '\n'
            fout = open('errororder.txt', 'w', encoding='utf-8')
            fout.write(sep.join(se_path_list))
            fout.close()

    def testAFLW(self, is_visualize=False):
        from data import matrix2Angle
        total_task = len(self.test_data)
        print('total img:', total_task)
        error_func_list = ['landmark2d', 'landmark3d', 'nme2d', 'nme3d', 'icp']
        # error_func_list = ['visiblekpt2d', 'visiblekpt3d', 'invisiblekpt2d', 'invisiblekpt3d']
        model = self.net.model
        total_error_list = []
        num_output = self.mode[3]
        num_input = self.mode[4]
        data_generator = DataGenerator(all_image_data=self.test_data, mode=self.mode[2], is_aug=False, is_pre_read=self.is_pre_read)

        pose_list = np.load('data/AFLW2000-3D.pose.npy')
        angle_arg = [[], [], []]  # [0,30]  [30,60]  [60,90]

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
                # outputs = model(x, *y)
                #
                # p = outputs[-1]
                p = model.predict(x)
                x = x.squeeze().cpu().numpy().transpose(1, 2, 0)
                p = p.squeeze().cpu().numpy().transpose(1, 2, 0) * 280
                b = sio.loadmat(self.test_data[i].bbox_info_path)
                # R = b['TformOffset'][0:3, 0:3].T
                # # yaw_angle = np.arctan2(-R[2, 0], np.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2))
                # # yaw_angle = np.abs(yaw_angle / np.pi * 180)

                yaw_angle = np.abs(pose_list[i])
                if yaw_angle <= 30:
                    angle_arg[0].append(i)
                elif yaw_angle <= 60:
                    angle_arg[1].append(i)
                elif yaw_angle <= 90:
                    angle_arg[2].append(i)
                gt_y = y[0]
                gt_y = gt_y.squeeze().cpu().numpy().transpose(1, 2, 0) * 280

                temp_errors = []
                for error_func_name in error_func_list:
                    error_func = getErrorFunction(error_func_name)
                    error = error_func(gt_y, p, b['Bbox'], b['Kpt'])
                    temp_errors.append(error)
                total_error_list.append(temp_errors)
                print(self.test_data[i].init_image_path, i, end='  ')
                for er in temp_errors:
                    print('%.5f' % er, end=' ')
                print('')


                if is_visualize:

                    if temp_errors[0] > 0.06:
                        init_image = np.load(self.test_data[i].cropped_image_path).astype(np.float32) / 255.0
                        diff = np.square(gt_y - p) * masks.face_mask_np3d
                        dist2d = np.sqrt(np.sum(diff[:, :, 0:2], axis=-1))
                        dist2d[0, 0] = 30.0
                        dist3d = np.sqrt(np.sum(diff[:, :, 0:3], axis=-1))
                        dist3d[0, 0] = 30.0
                        dist3 = np.sqrt(diff[:, :, 2])
                        dist3[0, 0] = 30.0
                        visibility = np.load(self.test_data[i].attention_mask_path.replace('attention', 'visibility')).astype(np.float32)

                        plt.subplot(2, 3, 1)
                        plt.imshow(init_image)
                        plt.subplot(2, 3, 2)
                        plt.imshow(dist2d)
                        plt.subplot(2, 3, 3)
                        plt.imshow(dist3d)
                        plt.subplot(2, 3, 4)
                        plt.imshow(dist3)
                        plt.subplot(2, 3, 5)
                        plt.imshow(visibility)
                        plt.show()

                        tex = np.load(self.test_data[i].texture_path.replace('zeroz2', 'full')).astype(np.float32)
                        init_image = np.load(self.test_data[i].cropped_image_path).astype(np.float32) / 255.0
                        show([p, tex, init_image], mode='uvmap')
                        init_image = np.load(self.test_data[i].cropped_image_path).astype(np.float32) / 255.0
                        show([gt_y, tex, init_image], mode='uvmap')
                # mean_errors = np.mean(total_error_list, axis=0)
                # for er in mean_errors:
                #     print('%.5f' % er, end=' ')
                print(i)
            mean_errors = np.mean(total_error_list, axis=0)
            for i in range(len(error_func_list)):
                print(error_func_list[i], mean_errors[i])

            total_error_list = np.array(total_error_list)
            error_list_30 = total_error_list[angle_arg[0]]
            error_list_60 = total_error_list[angle_arg[1]]
            error_list_90 = total_error_list[angle_arg[2]]
            print('length', len(angle_arg[2]))
            np.random.seed(0)
            np.random.shuffle(error_list_30)
            np.random.shuffle(error_list_60)
            np.random.shuffle(error_list_90)
            item_num = len(angle_arg[2])
            balance_error_list = np.concatenate([error_list_30[:item_num], error_list_60[:item_num], error_list_90[:item_num]])
            print(np.mean(error_list_30[:item_num], axis=0), '\n', np.mean(error_list_60[:item_num], axis=0), '\n', np.mean(error_list_90[:item_num], axis=0),
                  '\n', np.mean(balance_error_list, axis=0), '\n',
                  np.std(balance_error_list[:, 0], ddof=1))

    def testKPTV(self, is_visualize=False):
        from data import matrix2Angle
        total_task = len(self.test_data)
        print('total img:', total_task)
        # error_func_list = ['landmark2d', 'landmark3d', 'nme2d', 'nme3d', 'icp']
        error_func_list = ['visiblekpt2d', 'visiblekpt3d', 'invisiblekpt2d', 'invisiblekpt3d']
        model = self.net.model
        total_error_list = []
        num_output = self.mode[3]
        num_input = self.mode[4]
        data_generator = DataGenerator(all_image_data=self.test_data, mode=self.mode[2], is_aug=False, is_pre_read=self.is_pre_read)

        pose_list = np.load('data/AFLW2000-3D.pose.npy')
        angle_arg = [[], [], []]  # [0,30]  [30,60]  [60,90]

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
                outputs = model(x, *y)

                p = outputs[-1]
                x = x.squeeze().cpu().numpy().transpose(1, 2, 0)
                p = p.squeeze().cpu().numpy().transpose(1, 2, 0) * 280
                b = sio.loadmat(self.test_data[i].bbox_info_path)
                # R = b['TformOffset'][0:3, 0:3].T
                # # yaw_angle = np.arctan2(-R[2, 0], np.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2))
                # # yaw_angle = np.abs(yaw_angle / np.pi * 180)

                visibility_mask = np.load(self.test_data[i].bbox_info_path.replace('bbox_info.mat', 'visibility_mask.npy')).astype(np.float32)
                v_m = visibility_mask.copy()
                for ii in range(1, 254):
                    for jj in range(1, 254):
                        if visibility_mask[ii, jj] > 0:
                            v_m[ii - 1, jj] = 1
                            v_m[ii + 1, jj] = 1
                            v_m[ii, jj - 1] = 1
                            v_m[ii, jj + 1] = 1

                yaw_angle = np.abs(pose_list[i])
                if yaw_angle <= 30:
                    angle_arg[0].append(i)
                elif yaw_angle <= 60:
                    angle_arg[1].append(i)
                elif yaw_angle <= 90:
                    angle_arg[2].append(i)
                gt_y = y[0]
                gt_y = gt_y.squeeze().cpu().numpy().transpose(1, 2, 0) * 280

                temp_errors = []
                for error_func_name in error_func_list:
                    error_func = getErrorFunction(error_func_name)
                    error = error_func(gt_y, p, b['Bbox'], v_m)
                    if error == -1:
                        error = temp_errors[len(temp_errors) - 2]
                    temp_errors.append(error)
                total_error_list.append(temp_errors)
                print(self.test_data[i].init_image_path, end='  ')
                for er in temp_errors:
                    print('%.5f' % er, end=' ')
                print('')
                if is_visualize:

                    if temp_errors[0] > 0.06:
                        init_image = np.load(self.test_data[i].cropped_image_path).astype(np.float32) / 255.0
                        diff = np.square(gt_y - p) * masks.face_mask_np3d
                        dist2d = np.sqrt(np.sum(diff[:, :, 0:2], axis=-1))
                        dist2d[0, 0] = 30.0
                        dist3d = np.sqrt(np.sum(diff[:, :, 0:3], axis=-1))
                        dist3d[0, 0] = 30.0
                        dist3 = np.sqrt(diff[:, :, 2])
                        dist3[0, 0] = 30.0
                        visibility = np.load(self.test_data[i].attention_mask_path.replace('attention', 'visibility')).astype(np.float32)

                        plt.subplot(2, 3, 1)
                        plt.imshow(init_image)
                        plt.subplot(2, 3, 2)
                        plt.imshow(dist2d)
                        plt.subplot(2, 3, 3)
                        plt.imshow(dist3d)
                        plt.subplot(2, 3, 4)
                        plt.imshow(dist3)
                        plt.subplot(2, 3, 5)
                        plt.imshow(visibility)
                        plt.show()

                        tex = np.load(self.test_data[i].texture_path.replace('zeroz2', 'full')).astype(np.float32)
                        init_image = np.load(self.test_data[i].cropped_image_path).astype(np.float32) / 255.0
                        show([p, tex, init_image], mode='uvmap')
                        init_image = np.load(self.test_data[i].cropped_image_path).astype(np.float32) / 255.0
                        show([gt_y, tex, init_image], mode='uvmap')
                mean_errors = np.mean(total_error_list, axis=0)
                for er in mean_errors:
                    print('%.5f' % er, end=' ')
                print('')
            for i in range(len(error_func_list)):
                print(error_func_list[i], mean_errors[i])

            total_error_list = np.array(total_error_list)
            error_list_30 = total_error_list[angle_arg[0]]
            error_list_60 = total_error_list[angle_arg[1]]
            error_list_90 = total_error_list[angle_arg[2]]
            print('length', len(angle_arg[2]))
            np.random.seed(0)
            np.random.shuffle(error_list_30)
            np.random.shuffle(error_list_60)
            np.random.shuffle(error_list_90)
            item_num = len(angle_arg[2])
            balance_error_list = np.concatenate([error_list_30[:item_num], error_list_60[:item_num], error_list_90[:item_num]])
            print(np.mean(error_list_30[:item_num], axis=0), '\n', np.mean(error_list_60[:item_num], axis=0), '\n', np.mean(error_list_90[:item_num], axis=0),
                  '\n', np.mean(balance_error_list, axis=0), '\n',
                  np.std(balance_error_list[:, 0], ddof=1))

    def testMICC(self, is_visualize=False):

        error_func_list = ['micc']
        model = self.net.model
        total_error_list = []
        num_output = self.mode[3]
        num_input = self.mode[4]
        data_generator = DataGenerator(all_image_data=self.test_data, mode=self.mode[2], is_aug=False, is_pre_read=self.is_pre_read)

        pose_error_list = [[], [], [], [], []]

        with torch.no_grad():
            model.eval()
            for i in range(len(self.test_data)):
                image_id = int(self.test_data[i].cropped_image_path.split('/')[-2])
                image_pose_type = (image_id % 1000) // 4

                data = data_generator.__getitem__(i)
                x = data[0]
                x = x.to(self.net.device).float()
                y = [data[j] for j in range(1, 1 + num_input)]
                for j in range(num_input):
                    y[j] = y[j].to(x.device).float()
                    y[j] = torch.unsqueeze(y[j], 0)
                x = torch.unsqueeze(x, 0)
                outputs = model(x, *y)
                p = outputs[-1]
                x = x.squeeze().cpu().numpy().transpose(1, 2, 0)
                p = p.squeeze().cpu().numpy().transpose(1, 2, 0) * 280

                gt_mesh = np.load(self.test_data[i].cropped_image_path.replace('_cropped.npy', '_mesh.npy'))
                temp_errors = []
                for error_func_name in error_func_list:
                    error_func = getErrorFunction(error_func_name)
                    error = error_func(gt_mesh, p.copy())
                    temp_errors.append(error)
                total_error_list.append(temp_errors)

                # pose part
                pose_error_list[image_pose_type].append(temp_errors)

                print(self.test_data[i].init_image_path, end='  ')
                for er in temp_errors:
                    print('%.5f' % er, end=' ')
                print('')
                mean_errors = np.mean(total_error_list, axis=0)
                for er in mean_errors:
                    print('%.5f' % er, end=' ')
                print('')

                # pose part
                for pl in pose_error_list:
                    print('%.5f' % np.mean(np.array(pl)), end=' ')
                print('')

                if is_visualize and temp_errors[0] > 0.3:
                    tex = np.load('data/images/AFLW2000-full/image00002/image00002_uv_texture_map.npy').astype(np.float32)
                    init_image = np.load(self.test_data[i].cropped_image_path).astype(np.float32) / 255.0
                    show([p, tex, init_image], mode='uvmap')

            for i in range(len(error_func_list)):
                print(error_func_list[i], mean_errors[i])

            se_idx = np.argsort(np.sum(total_error_list, axis=-1))
            se_data_list = np.array(self.test_data)[se_idx]
            se_path_list = [a.cropped_image_path for a in se_data_list]
            sep = '\n'
            fout = open('errororder.txt', 'w', encoding='utf-8')
            fout.write(sep.join(se_path_list))
            fout.close()

    def testDemo(self, error_func_list=None, is_visualize=False):

        save_img_dir = 'saved_img/' + save_dir_time + self.net_structure + '/'
        if not os.path.exists('saved_img'):
            os.mkdir('saved_img')
        if not os.path.exists(save_img_dir):
            os.mkdir(save_img_dir)

        def kpt2Rotation(kpt_src, kpt_dst):
            A = kpt_src
            B = kpt_dst
            mu_A = A.mean(axis=0)
            mu_B = B.mean(axis=0)
            AA = A - mu_A
            BB = B - mu_B
            H = AA.T.dot(BB)
            U, S, Vt = np.linalg.svd(H)
            Rot = Vt.T.dot(U.T)
            # if np.linalg.det(R) < 0:
            #     print('singular R')
            #     Vt[2, :] *= -1
            #     R = Vt.T.dot(U.T)
            transac = mu_B - mu_A.dot(Rot.T)
            # tform = np.zeros((4, 4))
            # tform[0:3, 0:3] = R
            # tform[0:3, 3] = t
            # tform[3, 3] = 1
            return Rot, transac

        from data import mean_posmap
        norm_mean_posmap = mean_posmap * 8

        from loss import cp, uv_kpt
        from demorender import demoAll, compareKpt, renderCenter, demoKpt, renderLight
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
                specific_list = [  # 157, 285, 319, 574, 630, 835, 1300,
                    # 157,400,562,779,1078,1156,1328,1350,1496,1726,1838
                    # 331,360,364,388,397,465,630,637,664,779,891,955,1052,1281,1300,1328,1446,1453,1584,1712,1752
                    # 1776, 1766, 1462, 305, 1300, 835, 1914, 1751, 1721, 1607, 1561, 1496, 1478, 1446, 1424, 1415, 1409, 1380, 1363, 1328, 1273,
                    # 1247, 1194, 1184, 858, 762, 652, 630, 611, 574, 462, 450, 285, 157, 152, 36, 319
                    574
                ]
                # specific_list = [1300]
                if i not in specific_list:
                    continue

                data = data_generator.__getitem__(i)
                x = data[0]
                x = x.to(self.net.device).float()
                y = [data[j] for j in range(1, 1 + num_input)]
                for j in range(num_input):
                    y[j] = y[j].to(x.device).float()
                    y[j] = torch.unsqueeze(y[j], 0)
                x = torch.unsqueeze(x, 0)
                outputs = model(x, *y)
                p = outputs[-1]

                x = x.squeeze().cpu().numpy().transpose(1, 2, 0)
                p = p.squeeze().cpu().numpy().transpose(1, 2, 0) * 280
                b = sio.loadmat(self.test_data[i].bbox_info_path)
                gt_y = y[0]
                gt_y = gt_y.squeeze().cpu().numpy().transpose(1, 2, 0) * 280

                dst_kpt = norm_mean_posmap[uv_kpt[:, 0], uv_kpt[:, 1]]
                src_kpt = p[uv_kpt[:, 0], uv_kpt[:, 1]]
                R, T = kpt2Rotation(src_kpt, dst_kpt)
                p_norm = p.dot(R.T) + T
                p_norm[:, :, 1] = 256 - p_norm[:, :, 1]

                p_norm_gt = gt_y.dot(R.T) + T
                p_norm_gt[:, :, 1] = 256 - p_norm_gt[:, :, 1]

                # for PRN GT
                # Tform = cp(p[uv_kpt[:, 0], uv_kpt[:, 1], :], gt_y[uv_kpt[:, 0], uv_kpt[:, 1], :])
                # p = p.dot(Tform[0:3, 0:3].T) + Tform[0:3, 3]

                temp_errors = []
                for error_func_name in error_func_list:
                    error_func = getErrorFunction(error_func_name)
                    error = error_func(gt_y, p, b['Bbox'], b['Kpt'])
                    temp_errors.append(error)
                total_error_list.append(temp_errors)
                print(i, self.test_data[i].init_image_path, end='  ')
                for er in temp_errors:
                    print('%.5f' % er, end=' ')
                print('i')
                if is_visualize:
                    if temp_errors[0] >= 0.00:
                        demobg = np.load(self.test_data[i].cropped_image_path).astype(np.float32)
                        init_image = demobg / 255.0

                        # p2 = p.copy()
                        # for ii in range(1, 254):
                        #     for jj in range(1, 254):
                        #         p[ii, jj] = (p2[ii, jj] + p2[ii - 1, jj - 1] + p2[ii - 1, jj] + p2[ii - 1, jj + 1] +
                        #                      p2[ii + 1, jj - 1] + p2[ii + 1, jj] + p2[ii + 1, jj + 1]
                        #                      + p2[ii, jj - 1] + p2[ii, jj + 1]) / 9.0

                        img1, img2 = demoAll(p, demobg.copy(), is_render=False)
                        io.imsave(save_img_dir + str(i) + '_shape.jpg', img1)
                        io.imsave(save_img_dir + str(i) + '_kpt.jpg', img2.astype(np.uint8))
                        io.imsave(save_img_dir + str(i) + '_init.jpg', (init_image * 255).astype(np.uint8))

                        img1 = compareKpt(p, gt_y, demobg.copy(), is_render=False)
                        io.imsave(save_img_dir + str(i) + 'compare.jpg', img1.astype(np.uint8))

                        img3 = renderCenter(p_norm, is_render=False)
                        io.imsave(save_img_dir + str(i) + 'norm.jpg', img3)
                        img3 = renderCenter(p_norm_gt, is_render=False)
                        io.imsave(save_img_dir + str(i) + 'normgt.jpg', img3)

                        img5 = renderLight(gt_y, demobg.copy(), is_render=False)
                        io.imsave(save_img_dir + str(i) + 'gtshape.jpg', img5)

                        img6 = renderLight(p, None, False)
                        io.imsave(save_img_dir + str(i) + 'poseface.jpg', img6)

                        white = np.ones((256, 256, 3))
                        p_norm[:, :, 0] += 128
                        p_norm[:, :, 1] -= 128
                        img4 = demoKpt(p_norm, white, False)
                        io.imsave(save_img_dir + str(i) + 'normkpt.jpg', img4)

                        from data import getLandmark
                        gt_kpt = getLandmark(gt_y)
                        np.save(save_img_dir + str(i) + '_gtkpt.npy', gt_kpt)

                    # diff = np.square(gt_y - p) * masks.face_mask_np3d
                    # dist2d = np.sqrt(np.sum(diff[:, :, 0:2], axis=-1))
                    # dist2d[0, 0] = 30.0
                    # dist3d = np.sqrt(np.sum(diff[:, :, 0:3], axis=-1))
                    # dist3d[0, 0] = 30.0
                    # dist3 = np.sqrt(diff[:, :, 2])
                    # dist3[0, 0] = 30.0
                    # # visibility = np.load(self.test_data[i].attention_mask_path.replace('attention', 'visibility')).astype(np.float32)
                    #
                    # plt.subplot(2, 3, 1)
                    # plt.imshow(init_image)
                    # plt.subplot(2, 3, 2)
                    # plt.imshow(dist2d)
                    # plt.subplot(2, 3, 3)
                    # plt.imshow(dist3d)
                    # plt.subplot(2, 3, 4)
                    # plt.imshow(dist3)
                    # plt.subplot(2, 3, 5)
                    # # plt.imshow(visibility)
                    # # plt.show()
                    #
                    # tex = np.load(self.test_data[i].texture_path.replace('zeroz2', 'full')).astype(np.float32)
                    # init_image = np.load(self.test_data[i].cropped_image_path).astype(np.float32) / 255.0
                    # show([p, tex, init_image], mode='uvmap')
                    # init_image = np.load(self.test_data[i].cropped_image_path).astype(np.float32) / 255.0
                    # show([gt_y, tex, init_image], mode='uvmap')
                mean_errors = np.mean(total_error_list, axis=0)
                for er in mean_errors:
                    print('%.5f' % er, end=' ')
                print('')
            for i in range(len(error_func_list)):
                print(error_func_list[i], mean_errors[i])

    def testSpeed(self):
        total_task = len(self.test_data)
        print('total img:', total_task)

        model = self.net.model
        num_output = self.mode[3]
        num_input = self.mode[4]
        data_generator = DataGenerator(all_image_data=self.test_data, mode=self.mode[2], is_aug=False, is_pre_read=True)

        total_time = 0
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

                torch.cuda.synchronize()
                begin_time = time.time()

                model.predict(x)
                torch.cuda.synchronize()
                end_time = time.time()
                total_time = total_time + end_time - begin_time
                print(i + 1, total_time / (i + 1), end_time - begin_time)

                # for PRN GT
                # Tform = cp(p[uv_kpt[:, 0], uv_kpt[:, 1], :], gt_y[uv_kpt[:, 0], uv_kpt[:, 1], :])
                # p = p.dot(Tform[0:3, 0:3].T) + Tform[0:3, 3]

    def testErrorMask(self, error_func_list=None, is_visualize=False):
        from loss import cp, uv_kpt
        total_task = len(self.test_data)
        print('total img:', total_task)

        model = self.net.model
        total_error_list = []
        num_output = self.mode[3]
        num_input = self.mode[4]
        data_generator = DataGenerator(all_image_data=self.test_data, mode=self.mode[2], is_aug=False, is_pre_read=self.is_pre_read)
        error_func_list = ['errormask']
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
                outputs = model(x, *y)

                p = outputs[-1]
                x = x.squeeze().cpu().numpy().transpose(1, 2, 0)
                p = p.squeeze().cpu().numpy().transpose(1, 2, 0) * 280
                b = sio.loadmat(self.test_data[i].bbox_info_path)
                gt_y = y[0]
                gt_y = gt_y.squeeze().cpu().numpy().transpose(1, 2, 0) * 280

                # for PRN GT
                # Tform = cp(p[uv_kpt[:, 0], uv_kpt[:, 1], :], gt_y[uv_kpt[:, 0], uv_kpt[:, 1], :])
                # p = p.dot(Tform[0:3, 0:3].T) + Tform[0:3, 3]

                temp_errors = []
                for error_func_name in error_func_list:
                    error_func = getErrorFunction(error_func_name)
                    error = error_func(gt_y, p, b['Bbox'], b['Kpt'])
                    temp_errors.append(error)
                total_error_list.append(temp_errors)
                print(self.test_data[i].init_image_path, end='  ')
                # for er in temp_errors:
                #     print('%.5f' % er, end=' ')
                # print(i)

                # for er in mean_errors:
                #     print('%.5f' % er, end=' ')
                # print('')
            # for i in range(len(error_func_list)):
            #     print(error_func_list[i], mean_errors[i])
            mean_errors = np.mean(total_error_list, axis=0)
            np.save('error_mask.npy', mean_errors)

    def annotationLS3D(self, error_func_list=None, is_visualize=False):
        from loss import cp, uv_kpt
        total_task = len(self.test_data)
        print('total img:', total_task)

        model = self.net.model
        total_error_list = []
        num_output = self.mode[3]
        num_input = self.mode[4]
        data_generator = DataGenerator(all_image_data=self.test_data, mode=self.mode[2], is_aug=False, is_pre_read=False)

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
                outputs = model(x, *y)

                p = outputs[-1]
                p = p.squeeze().cpu().numpy().transpose(1, 2, 0) * 280
                fit_kpt = p[uv_kpt[:, 0], uv_kpt[:, 1]].astype(np.float32)

                info_path = self.test_data[i].bbox_info_path
                kpt = sio.loadmat(info_path)['Kpt'].astype(np.float32)
                print(kpt.shape, fit_kpt.shape)
                final_kpt = np.concatenate((kpt, fit_kpt[:, 2:]), axis=1)
                np.save(info_path.replace('bbox_info.mat', 'kpt.npy'), final_kpt)
                print('\r', i, info_path, end='')


if __name__ == '__main__':
    random.seed(0)
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
    parser.add_argument('-aflw', '--isTestAFLW', default=False, type=ast.literal_eval, help='')
    parser.add_argument('-micc', '--isTestMICC', default=False, type=ast.literal_eval, help='')
    parser.add_argument('-demo', '--isTestDemo', default=False, type=ast.literal_eval, help='')
    parser.add_argument('-speed', '--isTestSpeed', default=False, type=ast.literal_eval, help='')
    parser.add_argument('-annot', '--isAnnotation', default=False, type=ast.literal_eval, help='')
    parser.add_argument('-errormask', '--isErrorMask', default=False, type=ast.literal_eval)
    parser.add_argument('-tkptv', '--isKPTV', default=False, type=ast.literal_eval, help='')
    parser.add_argument('-testsingle', '--isTestSingle', default=False, type=ast.literal_eval, help='')
    parser.add_argument('-visualize', '--isVisualize', default=False, type=ast.literal_eval, help='')
    parser.add_argument('--errorFunction', default='nme2d', nargs='+', type=str)
    parser.add_argument('--loadModelPath', default=None, type=str, help='')
    parser.add_argument('--visibleDevice', default='0', type=str, help='')
    parser.add_argument('-struct', '--netStructure', default='InitPRN', type=str, help='')
    parser.add_argument('-lr', '--learningRate', default=1e-4, type=float)
    parser.add_argument('--startEpoch', default=0, type=int)
    parser.add_argument('--isPreRead', default=True, type=ast.literal_eval)
    parser.add_argument('--numWorker', default=4, type=int, help='loader worker number')

    run_args = parser.parse_args()

    print(run_args)

    os.environ["CUDA_VISIBLE_DEVICES"] = run_args.visibleDevice
    print(torch.cuda.is_available(), torch.cuda.device_count(), torch.cuda.current_device(), torch.cuda.get_device_name(0))
    save_dir_time = save_dir_time + run_args.netStructure

    net_manager = NetworkManager(run_args)
    net_manager.buildModel(run_args)
    if run_args.isTrain:
        if run_args.trainDataDir is not None:
            if run_args.valDataDir is not None:
                for dir in run_args.trainDataDir:
                    net_manager.addImageData(dir, 'train')
                for dir in run_args.valDataDir:
                    net_manager.addImageData(dir, 'val')
            else:
                for dir in run_args.trainDataDir:
                    net_manager.addImageData(dir, 'both')
            net_manager.saveImageDataPaths()
        else:
            net_manager.loadImageDataPaths()

        if run_args.loadModelPath is not None:
            net_manager.net.loadWeights(run_args.loadModelPath)
        net_manager.train()

    if run_args.isTest:
        for dir in run_args.testDataDir:
            net_manager.addImageData(dir, 'test')
        if run_args.loadModelPath is not None:
            net_manager.net.loadWeights(run_args.loadModelPath)
            net_manager.test(error_func_list=run_args.errorFunction, is_visualize=run_args.isVisualize)

    if run_args.isTestAFLW:
        for dir in run_args.testDataDir:
            net_manager.addImageData(dir, 'test')
        if run_args.loadModelPath is not None:
            net_manager.net.loadWeights(run_args.loadModelPath)
            net_manager.testAFLW(is_visualize=run_args.isVisualize)

    if run_args.isTestMICC:
        for dir in run_args.testDataDir:
            net_manager.addImageData(dir, 'test')
        if run_args.loadModelPath is not None:
            net_manager.net.loadWeights(run_args.loadModelPath)
            net_manager.testMICC(is_visualize=run_args.isVisualize)

    if run_args.isTestDemo:
        for dir in run_args.testDataDir:
            net_manager.addImageData(dir, 'test')
        if run_args.loadModelPath is not None:
            net_manager.net.loadWeights(run_args.loadModelPath)
            net_manager.testDemo(error_func_list=run_args.errorFunction, is_visualize=True)

    if run_args.isAnnotation:
        for dir in run_args.testDataDir:
            net_manager.addImageData(dir, 'test')
        if run_args.loadModelPath is not None:
            net_manager.net.loadWeights(run_args.loadModelPath)
            net_manager.annotationLS3D(error_func_list=run_args.errorFunction, is_visualize=False)

    if run_args.isTestSpeed:
        for dir in run_args.testDataDir:
            net_manager.addImageData(dir, 'test')
            net_manager.testSpeed()
    if run_args.isKPTV:
        for dir in run_args.testDataDir:
            net_manager.addImageData(dir, 'test')
        if run_args.loadModelPath is not None:
            net_manager.net.loadWeights(run_args.loadModelPath)
            net_manager.testKPTV(is_visualize=run_args.isVisualize)
    if run_args.isErrorMask:
        for dir in run_args.testDataDir:
            net_manager.addImageData(dir, 'test')
        if run_args.loadModelPath is not None:
            net_manager.net.loadWeights(run_args.loadModelPath)
            net_manager.testErrorMask(error_func_list=run_args.errorFunction)

    writer.close()
