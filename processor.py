import os
import numpy as np
import scipy.io as sio
from skimage import io, transform
import skimage
from faceutil import mesh
import argparse
import ast
import copy
import multiprocessing
import math
from data import default_init_image_shape, default_cropped_image_shape, default_uvmap_shape, uv_coords, bfm
from data import face_mask_np, face_mask_mean_fix_rate
from data import bfm2Mesh, mesh2UVmap, UVmap2Mesh, renderMesh, getTransformMatrix
from augmentation import getRotateMatrix, getRotateMatrix3D
from numpy.linalg import inv
from masks import getImageAttentionMask, getVisibilityMask
import torchfile


class DataProcessor:
    def __init__(self,
                 is_full_image=False, is_visualize=True, is_augment=False, is_pt3d=False, is_offset=False,
                 bbox_extend_rate=1.5, marg_rate=0.1, is_zero_z=False):

        print('bfm model loaded')

        self.image_file_name = ''
        self.image_name = ''
        self.image_path = ''
        self.image_dir = ''
        self.output_dir = ''  # output_dir/image_name/image_name_xxxx.xxx
        self.write_dir = ''  # write_dir/image_name_xxxx.xxx

        self.init_image = None
        self.image_shape = None
        self.bfm_info = None
        self.uv_position_map = None
        self.uv_texture_map = None
        self.mesh_info = None

        self.is_full_image = is_full_image
        self.is_visualize = is_visualize,
        self.bbox_extend_rate = bbox_extend_rate
        self.marg_rate = marg_rate
        self.is_augment = is_augment
        self.is_pt3d = is_pt3d
        self.is_offset = is_offset
        self.is_zero_z = is_zero_z

    def initialize(self, image_path, output_dir='data/temp'):
        self.image_path = image_path
        self.image_file_name = image_path.strip().split('/')[-1]
        self.image_name = self.image_file_name.split('.')[0]
        self.output_dir = output_dir
        if not os.path.exists(output_dir):
            print('mkdir ', output_dir)
            os.mkdir(output_dir)
        if not os.path.exists(output_dir + '/' + self.image_name):
            os.mkdir(output_dir + '/' + self.image_name)
        self.write_dir = output_dir + '/' + self.image_name

        self.init_image = io.imread(self.image_path) / 255.
        self.image_shape = self.init_image.shape

    @staticmethod
    def getBbox(kpt):
        left = np.min(kpt[:, 0])
        right = np.max(kpt[:, 0])
        top = np.min(kpt[:, 1])
        bottom = np.max(kpt[:, 1])
        return left, top, right, bottom

    def getCropBox(self, bbox):
        [left, top, right, bottom] = bbox
        center = np.array([right - (right - left) / 2.0, bottom - (bottom - top) / 2.0])
        old_size = (right - left + bottom - top) / 2.0
        size = int(old_size * self.bbox_extend_rate)  # 1.5
        marg = old_size * self.marg_rate  # 0.1
        t_x = np.random.rand() * marg * 2 - marg
        t_y = np.random.rand() * marg * 2 - marg
        center[0] = center[0] + t_x
        center[1] = center[1] + t_y
        size = size * (np.random.rand() * 2 * self.marg_rate - self.marg_rate + 1)
        return center, size

    def runPosmap(self):
        # 1. load image and fitted parameters
        [height, width, channel] = self.image_shape
        pose_para = self.bfm_info['Pose_Para'].T.astype(np.float32)
        shape_para = self.bfm_info['Shape_Para'].astype(np.float32)
        exp_para = self.bfm_info['Exp_Para'].astype(np.float32)
        vertices = bfm.generate_vertices(shape_para, exp_para)
        # transform mesh

        s = pose_para[-1, 0]
        angles = pose_para[:3, 0]
        t = pose_para[3:6, 0]
        transformed_vertices = bfm.transform_3ddfa(vertices, s, angles, t)
        projected_vertices = transformed_vertices.copy()  # using stantard camera & orth projection as in 3DDFA
        image_vertices = projected_vertices.copy()
        image_vertices[:, 1] = height - image_vertices[:, 1]

        # 3. crop image with key points
        # 3.1 get old bbox
        kpt = image_vertices[bfm.kpt_ind, :].astype(np.int32)
        [left, top, right, bottom] = self.getBbox(kpt)
        old_bbox = np.array([[left, top], [right, bottom]])

        # 3.2 add margin to bbox
        center = np.array([right - (right - left) / 2.0, bottom - (bottom - top) / 2.0])
        old_size = (right - left + bottom - top) / 2
        size = int(old_size * self.bbox_extend_rate)  # 1.5
        marg = old_size * self.marg_rate  # 0.1
        t_x = np.random.rand() * marg * 2 - marg
        t_y = np.random.rand() * marg * 2 - marg
        center[0] = center[0] + t_x
        center[1] = center[1] + t_y
        size = size * (np.random.rand() * 2 * self.marg_rate - self.marg_rate + 1)

        # 3.3 crop and record the transform parameters
        [crop_h, crop_w, crop_c] = default_cropped_image_shape
        src_pts = np.array([[center[0] - size / 2, center[1] - size / 2], [center[0] - size / 2, center[1] + size / 2],
                            [center[0] + size / 2, center[1] - size / 2]])
        dst_pts = np.array([[0, 0], [0, crop_h - 1], [crop_w - 1, 0]])
        tform = skimage.transform.estimate_transform('similarity', src_pts, dst_pts)
        trans_mat = tform.params
        trans_mat_inv = tform._inv_matrix
        scale = trans_mat[0][0]
        if self.is_augment:
            # do rotation
            if np.random.rand() > 0.5:
                angle = np.random.randint(-90, 90)
            else:
                angle = 0
            angle = angle / 180. * np.pi
            [rt_mat, rt_mat_inv] = getRotateMatrix(angle, [crop_h, crop_w, crop_c])
            trans_mat = rt_mat.dot(trans_mat)
            trans_mat_inv = trans_mat_inv.dot(rt_mat_inv)
        cropped_image = skimage.transform.warp(self.init_image, trans_mat_inv, output_shape=(crop_h, crop_w))
        # 3.4 transform face position(image vertices)
        position = image_vertices.copy()
        position[:, 2] = 1
        position = np.dot(position, trans_mat.T)
        position[:, 2] = image_vertices[:, 2] * scale  # scale z
        position[:, 2] = position[:, 2] - np.min(position[:, 2])  # translate z

        # 4. uv position map: render position in uv space
        [uv_h, uv_w, uv_c] = default_uvmap_shape
        uv_position_map = mesh.render.render_colors(uv_coords, bfm.full_triangles, position, uv_h,
                                                    uv_w, uv_c)

        # get new bbox
        kpt = position[bfm.kpt_ind, :].astype(np.int32)
        [left, top, right, bottom] = self.getBbox(kpt)
        bbox = np.array([[left, top], [right, bottom]])

        if self.is_pt3d:
            # get gt landmark68
            init_kpt = self.bfm_info['pt3d_68'].T
            init_kpt[:, 2] = init_kpt[:, 2] - np.min(image_vertices[:, 2])
            new_kpt = copy.copy(init_kpt)
            new_kpt[:, 2] = 1
            new_kpt = np.dot(new_kpt, trans_mat.T)
            new_kpt[:, 2] = init_kpt[:, 2] * scale
        else:
            new_kpt = []
            init_kpt = []

        # from datavisualize import showMesh, show
        # show([uv_position_map, None, cropped_image], False, 'uvmap')
        # 5. save files
        sio.savemat(self.write_dir + '/' + self.image_name + '_bbox_info.mat',
                    {'OldBbox': old_bbox, 'Bbox': bbox, 'Tform': trans_mat, 'TformInv': trans_mat_inv,
                     'Kpt': new_kpt,
                     'OldKpt': init_kpt})
        np.save(self.write_dir + '/' + self.image_name + '_cropped_uv_posmap.npy', uv_position_map)
        io.imsave(self.write_dir + '/' + self.image_name + '_cropped.jpg',
                  (np.squeeze(cropped_image * 255.0)).astype(np.uint8))

    def runOffsetPosmap(self):
        # 1. load image and fitted parameters
        [height, _, _] = self.image_shape
        pose_para = self.bfm_info['Pose_Para'].T.astype(np.float32)
        shape_para = self.bfm_info['Shape_Para'].astype(np.float32)
        exp_para = self.bfm_info['Exp_Para'].astype(np.float32)
        vertices = bfm.generate_vertices(shape_para, exp_para)
        offset_vertices = bfm.generate_offset(shape_para, exp_para)

        s = pose_para[-1, 0]
        angles = pose_para[:3, 0]
        t = pose_para[3:6, 0]

        if self.is_zero_z:
            t[2] = 0

        T_bfm = getTransformMatrix(s, angles, t, height)
        temp_ones_vec = np.ones((len(vertices), 1))
        homo_vertices = np.concatenate((vertices, temp_ones_vec), axis=-1)
        image_vertices = homo_vertices.dot(T_bfm.T)[:, 0:3]

        # 3. crop image with key points
        # 3.1 get old bbox
        kpt = image_vertices[bfm.kpt_ind, :].astype(np.int32)
        [left, top, right, bottom] = self.getBbox(kpt)
        old_bbox = np.array([[left, top], [right, bottom]])

        # 3.2 add margin to bbox
        [center, size] = self.getCropBox([left, top, right, bottom])

        # 3.3 crop and record the transform parameters
        [crop_h, crop_w, _] = default_cropped_image_shape

        T_3d = np.zeros((4, 4))
        T_3d[0, 0] = crop_w / size
        T_3d[1, 1] = crop_h / size
        T_3d[2, 2] = crop_w / size
        T_3d[3, 3] = 1.
        if self.is_zero_z:
            T_3d[0:3, 3] = [(size / 2 - center[0]) * crop_w / size, (size / 2 - center[1]) * crop_h / size, 0]
        else:
            T_3d[0:3, 3] = [(size / 2 - center[0]) * crop_w / size, (size / 2 - center[1]) * crop_h / size, -np.min(image_vertices[:, 2]) * crop_w / size]
        T_2d = np.zeros((3, 3))
        T_2d[0:2, 0:2] = T_3d[0:2, 0:2]
        T_2d[2, 2] = 1.
        T_2d[0:2, 2] = T_3d[0:2, 3]

        # if self.is_augment:
        #     angle = np.random.randint(-45, 45)
        #     angle = angle / 180. * np.pi
        #     [T_rotate_2d, _] = getRotateMatrix(angle, [crop_h, crop_w, crop_c])
        #     T_2d = T_rotate_2d.dot(T_2d)
        #     [T_rotate_3d, _] = getRotateMatrix3D(angle, [crop_h, crop_w, crop_c])
        #     T_3d = T_rotate_3d.dot(T_3d)

        T_2d_inv = inv(T_2d)
        cropped_image = skimage.transform.warp(self.init_image, T_2d_inv, output_shape=(crop_h, crop_w))
        # 3.4 transform face position(image vertices)

        p4d = np.concatenate((image_vertices, temp_ones_vec), axis=-1)
        position = p4d.dot(T_3d.T)[:, 0:3]

        offset_position = offset_vertices * 1e-4
        # T_scale_1e4 = np.diagflat([1e4, 1e4, 1e4, 1])
        # mean_position = bfm.get_mean_shape()
        # rebuild_position = np.concatenate((mean_position * 1e-4 + offset_position, temp_ones_vec), axis=-1).dot(T_3d.dot(T_bfm).dot(T_scale_1e4).T)[:, 0:3]
        # diff = rebuild_position - position

        # 4. uv position map: render position in uv space
        [uv_h, uv_w, uv_c] = default_uvmap_shape
        uv_position_map = mesh.render.render_colors(uv_coords, bfm.full_triangles, position, uv_h,
                                                    uv_w, uv_c)

        uv_offset_map = mesh.render.render_colors(uv_coords, bfm.full_triangles, offset_position, uv_h,
                                                  uv_w, uv_c)

        # get new bbox
        kpt = position[bfm.kpt_ind, :].astype(np.int32)
        [left, top, right, bottom] = self.getBbox(kpt)
        bbox = np.array([[left, top], [right, bottom]])

        if self.is_pt3d:
            # get gt landmark68
            # init_kpt = self.bfm_info['pt3d_68'].T
            init_kpt = image_vertices[bfm.kpt_ind, :]
            init_kpt_4d = np.concatenate((init_kpt, np.ones((68, 1))), axis=-1)
            new_kpt = init_kpt_4d.dot(T_3d.T)[:, 0:3]
        else:
            new_kpt = []
            init_kpt = []

        # if self.is_augment:
        #     cropped_image = unchangeAugment(cropped_image)
        # cropped_image = gaussNoise(cropped_image)
        # 5. save files
        attention_mask = getImageAttentionMask(cropped_image, uv_position_map)
        np.save(self.write_dir + '/' + self.image_name + '_attention_mask.npy', attention_mask.astype(np.uint8))
        if self.is_augment:
            visibility_mask = getVisibilityMask(uv_position_map, cropped_image.shape)
            np.save(self.write_dir + '/' + self.image_name + '_visibility_mask.npy', visibility_mask.astype(np.uint8))

        sio.savemat(self.write_dir + '/' + self.image_name + '_bbox_info.mat',
                    {'OldBbox': old_bbox, 'Bbox': bbox, 'Tform': T_2d.astype(np.float32), 'TformInv': T_2d_inv.astype(np.float32),
                     'Tform3d': T_3d.astype(np.float32), 'Kpt': new_kpt, 'OldKpt': init_kpt,
                     'TformOffset': T_3d.dot(T_bfm).astype(np.float32)})
        np.save(self.write_dir + '/' + self.image_name + '_cropped_uv_posmap.npy', uv_position_map.astype(np.float32))
        np.save(self.write_dir + '/' + self.image_name + '_offset_posmap.npy', uv_offset_map.astype(np.float32))
        io.imsave(self.write_dir + '/' + self.image_name + '_cropped.jpg', (np.squeeze(cropped_image * 255.0)).astype(np.uint8))
        np.save(self.write_dir + '/' + self.image_name + '_cropped.npy', (np.squeeze(cropped_image * 255.0)).astype(np.uint8))

    def processImage(self, image_path, output_dir):
        self.initialize(image_path, output_dir)
        self.bfm_info = sio.loadmat(self.image_path.replace('.jpg', '.mat'))
        if self.is_full_image:
            self.mesh_info = bfm2Mesh(self.bfm_info)
            [self.uv_position_map, self.uv_texture_map] = mesh2UVmap(self.mesh_info)
            io.imsave(self.write_dir + '/' + self.image_name + '_init.jpg', (self.init_image * 255.0).clip(0, 255).astype(np.uint8))
            sio.savemat(self.write_dir + '/' + self.image_name + '_mesh.mat', self.mesh_info)
            np.save(self.write_dir + '/' + self.image_name + '_uv_posmap.npy', self.uv_position_map)
            np.save(self.write_dir + '/' + self.image_name + '_uv_texture_map.npy', self.uv_texture_map)
            if self.is_visualize:
                mesh_image = renderMesh(self.mesh_info, self.init_image.shape)
                io.imsave(self.write_dir + '/' + self.image_name + '_generate.jpg', (mesh_image * 255.0).clip(0, 255).astype(np.uint8))
                uv_texture_map = np.clip(self.uv_texture_map, 0., 1.)
                io.imsave(self.write_dir + '/' + self.image_name + '_uv_texture_map.jpg', (uv_texture_map * 255.0).clip(0, 255).astype(np.uint8))

        if self.is_offset:
            self.runOffsetPosmap()
        # else:
        #     self.runPosmap()
        self.clear()

    def runLS3D(self, image_path, output_dir):
        if os.path.exists(image_path.replace('.jpg', '.t7')):
            self.initialize(image_path, output_dir)
            kpt = torchfile.load(self.image_path.replace('.jpg', '.t7'))
        elif os.path.exists(image_path.replace('.png', '.t7')):
            return
            self.initialize(image_path, output_dir)
            kpt = torchfile.load(self.image_path.replace('.png', '.t7'))
        else:
            print('\nnot exist', image_path, image_path.replace('.jpg', '.t7'), '\n')
            return

        if kpt.shape[1] != 2:
            print('error not 2d kpt\n\n', image_path)
        [left, top, right, bottom] = self.getBbox(kpt)
        old_bbox = np.array([[left, top], [right, bottom]])

        # 3.2 add margin to bbox
        [center, size] = self.getCropBox([left, top, right, bottom])

        # 3.3 crop and record the transform parameters
        [crop_h, crop_w, _] = default_cropped_image_shape

        T_3d = np.zeros((4, 4))
        T_3d[0, 0] = crop_w / size
        T_3d[1, 1] = crop_h / size
        T_3d[2, 2] = crop_w / size
        T_3d[3, 3] = 1.

        T_3d[0:3, 3] = [(size / 2 - center[0]) * crop_w / size, (size / 2 - center[1]) * crop_h / size, 0]

        T_2d = np.zeros((3, 3))
        T_2d[0:2, 0:2] = T_3d[0:2, 0:2]
        T_2d[2, 2] = 1.
        T_2d[0:2, 2] = T_3d[0:2, 3]

        T_2d_inv = inv(T_2d)
        cropped_image = skimage.transform.warp(self.init_image, T_2d_inv, output_shape=(crop_h, crop_w))

        kpt3d = np.concatenate((kpt, np.ones((68, 1))), axis=-1)
        new_kpt = kpt3d.dot(T_2d.T)[:, 0:2]

        [left, top, right, bottom] = self.getBbox(new_kpt)
        bbox = np.array([[left, top], [right, bottom]])

        sio.savemat(self.write_dir + '/' + self.image_name + '_bbox_info.mat',
                    {'OldBbox': old_bbox, 'Bbox': bbox, 'Tform': T_2d.astype(np.float32), 'TformInv': T_2d_inv.astype(np.float32),
                     'Tform3d': T_3d.astype(np.float32), 'Kpt': new_kpt, 'OldKpt': kpt,
                     'TformOffset': T_3d.astype(np.float32)})
        io.imsave(self.write_dir + '/' + self.image_name + '_cropped.jpg', (np.squeeze(cropped_image * 255.0)).astype(np.uint8))
        np.save(self.write_dir + '/' + self.image_name + '_cropped.npy', (np.squeeze(cropped_image * 255.0)).astype(np.uint8))

    def clear(self):
        self.image_file_name = ''
        self.image_name = ''
        self.image_path = ''
        self.image_dir = ''
        self.output_dir = ''
        self.write_dir = ''

        self.init_image = None
        self.image_shape = None
        self.bfm_info = None
        self.uv_position_map = None
        self.uv_texture_map = None
        self.mesh_info = None


def workerProcess(image_paths, output_dirs, worker_id, worker_conf):
    print('worker:', worker_id, 'start. task number:', len(image_paths))
    data_processor = DataProcessor(bbox_extend_rate=worker_conf.bboxExtendRate, marg_rate=worker_conf.margin, is_pt3d=worker_conf.isOldKpt,
                                   is_visualize=worker_conf.isVisualize, is_full_image=worker_conf.isFull, is_augment=worker_conf.isAugment,
                                   is_offset=worker_conf.isOffset, is_zero_z=worker_conf.isZeroZ)
    if worker_conf.isLS3D:

        for i in range(len(image_paths)):
            # print('\r worker ' + str(id) + ' task ' + str(i) + '/' + str(len(image_paths)) +''+  image_paths[i])
            print("worker {} task {}/{}  {}\r".format(str(worker_id), str(i), str(len(image_paths)), image_paths[i]), end='')
            # output_list[id] = "worker {} task {}/{}  {}".format(str(id), str(i), str(len(image_paths)), image_paths[i])
            data_processor.runLS3D(image_paths[i], output_dirs[i])
    else:
        for i in range(len(image_paths)):
            # print('\r worker ' + str(id) + ' task ' + str(i) + '/' + str(len(image_paths)) +''+  image_paths[i])
            print("worker {} task {}/{}  {}\r".format(str(worker_id), str(i), str(len(image_paths)), image_paths[i]), end='')
            # output_list[id] = "worker {} task {}/{}  {}".format(str(id), str(i), str(len(image_paths)), image_paths[i])
            data_processor.processImage(image_paths[i], output_dirs[i])
    print('worker:', worker_id, 'end')


def multiProcess(thread_conf):
    worker_num = thread_conf.thread
    input_dir = thread_conf.inputDir
    output_dir = thread_conf.outputDir
    image_path_list = []
    output_dir_list = []

    if not os.path.exists(output_dir):
        os.mkdir(output_dir)
    print(input_dir)
    for root, dirs, files in os.walk(input_dir):
        temp_output_dir = output_dir
        # tokens = root.split(input_dir)
        if not (root.split(input_dir)[1] == ''):
            temp_output_dir = output_dir + root.split(input_dir)[1]
            if not os.path.exists(temp_output_dir):
                os.mkdir(temp_output_dir)

        for file in files:
            file_tokens = file.split('.')
            file_type = file_tokens[1]
            if file_type == 'jpg' or file_type == 'png':
                image_path_list.append(root + '/' + file)
                output_dir_list.append(temp_output_dir)

    total_task = len(image_path_list)
    print('found images:', total_task)

    if worker_num <= 1:
        workerProcess(image_path_list, output_dir_list, 0, thread_conf)
    elif worker_num > 1:
        jobs = []
        task_per_worker = math.ceil(total_task / worker_num)
        st_idx = [task_per_worker * i for i in range(worker_num)]
        ed_idx = [min(total_task, task_per_worker * (i + 1)) for i in range(worker_num)]
        for i in range(worker_num):
            # temp_data_processor = copy.deepcopy(data_processor)
            p = multiprocessing.Process(target=workerProcess, args=(
                image_path_list[st_idx[i]:ed_idx[i]],
                output_dir_list[st_idx[i]:ed_idx[i]], i, thread_conf))
            jobs.append(p)
            p.start()


if __name__ == "__main__":
    # showModel("data/images/AFLW2000/image00107.jpg", "data/images/AFLW2000/image00107.mat", True)
    # multiProcess("data/images/300W-3D", "data/images/300W-3D-crop", worker_num=8)
    # multiProcess("data/images/AFLW2000", "data/images/AFLW2000-crop", worker_num=2)
    # multiProcess("data/images/300W_LP", "data/images/300W_LP-crop", worker_num=32)
    parser = argparse.ArgumentParser(
        description='data preprocess arguments')

    parser.add_argument('-i', '--inputDir', default='data/images/AFLW2000', type=str,
                        help='path to the input directory, where input images are stored.')
    parser.add_argument('-o', '--outputDir', default='data/images/AFLW2000-crop', type=str,
                        help='path to the output directory, where results(npy,cropped jpg) will be stored.')
    parser.add_argument('-s', '--isSingle', default=False, type=ast.literal_eval,
                        help='processs one image or all images in a directory')
    parser.add_argument('-t', '--thread', default='1', type=int,
                        help='thread number for multiprocessing')

    parser.add_argument('-f', '--isFull', default=False, type=ast.literal_eval,
                        help='whether to process init image')
    parser.add_argument('-v', '--isVisualize', default=False, type=ast.literal_eval,
                        help='whether to save images of some data such as texture')

    parser.add_argument('-b', '--bboxExtendRate', default=1.5, type=float,
                        help='extend rate of bounding box of cropped face')
    parser.add_argument('-m', '--margin', default=0.1, type=float,
                        help='margin for the bbox')
    parser.add_argument('-a', '--isAugment', default=False, type=ast.literal_eval,
                        help='do augmentation or not')
    parser.add_argument('--isOldKpt', default=False, type=ast.literal_eval,
                        help='for 300W there is no pt68_3d')
    parser.add_argument('--isOffset', default=True, type=ast.literal_eval)
    parser.add_argument('--isZeroZ', default=False, type=ast.literal_eval)
    parser.add_argument('--isLS3D', default=False, type=ast.literal_eval)
    conf = parser.parse_args()

    if not conf.isSingle:
        multiProcess(conf)
    else:
        workerProcess([conf.inputDir], [conf.outputDir], 0, conf)
