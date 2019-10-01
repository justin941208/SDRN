import numpy as np
from skimage import io, transform
import math
import copy
from PIL import ImageEnhance, ImageOps, ImageFile, Image
from imgaug import augmenters as iaa
import cv2


# import numba


# sometimes = lambda aug: iaa.Sometimes(0.5, aug)


def randomColor(image):
    """
    """
    PIL_image = Image.fromarray((image * 255.).astype(np.uint8))
    random_factor = np.random.randint(0, 31) / 10.
    color_image = ImageEnhance.Color(PIL_image).enhance(random_factor)  # 调整图像的饱和度
    random_factor = np.random.randint(10, 21) / 10.
    brightness_image = ImageEnhance.Brightness(color_image).enhance(random_factor)  # 调整图像的亮度
    random_factor = np.random.randint(10, 21) / 10.
    contrast_image = ImageEnhance.Contrast(brightness_image).enhance(random_factor)  # 调整图像对比度
    random_factor = np.random.randint(0, 31) / 10.
    out = np.array(ImageEnhance.Sharpness(contrast_image).enhance(random_factor))
    out = out / 255.
    return out


def getRotateMatrix(angle, image_shape):
    [image_height, image_width, image_channel] = image_shape
    t1 = np.array([[1, 0, -image_height / 2.], [0, 1, -image_width / 2.], [0, 0, 1]])
    r1 = np.array([[math.cos(angle), math.sin(angle), 0], [math.sin(-angle), math.cos(angle), 0], [0, 0, 1]])
    t2 = np.array([[1, 0, image_height / 2.], [0, 1, image_width / 2.], [0, 0, 1]])
    rt_mat = t2.dot(r1).dot(t1)
    t1 = np.array([[1, 0, -image_height / 2.], [0, 1, -image_width / 2.], [0, 0, 1]])
    r1 = np.array([[math.cos(-angle), math.sin(-angle), 0], [math.sin(angle), math.cos(-angle), 0], [0, 0, 1]])
    t2 = np.array([[1, 0, image_height / 2.], [0, 1, image_width / 2.], [0, 0, 1]])
    rt_mat_inv = t2.dot(r1).dot(t1)
    return rt_mat.astype(np.float32), rt_mat_inv.astype(np.float32)


def getRotateMatrix3D(angle, image_shape):
    [image_height, image_width, image_channel] = image_shape
    t1 = np.array([[1, 0, 0, -image_height / 2.], [0, 1, 0, -image_width / 2.], [0, 0, 1, 0], [0, 0, 0, 1]])
    r1 = np.array([[math.cos(angle), math.sin(angle), 0, 0], [math.sin(-angle), math.cos(angle), 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    t2 = np.array([[1, 0, 0, image_height / 2.], [0, 1, 0, image_width / 2.], [0, 0, 1, 0], [0, 0, 0, 1]])
    rt_mat = t2.dot(r1).dot(t1)
    t1 = np.array([[1, 0, 0, -image_height / 2.], [0, 1, 0, -image_width / 2.], [0, 0, 1, 0], [0, 0, 0, 1]])
    r1 = np.array([[math.cos(-angle), math.sin(-angle), 0, 0], [math.sin(angle), math.cos(-angle), 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    t2 = np.array([[1, 0, 0, image_height / 2.], [0, 1, 0, image_width / 2.], [0, 0, 1, 0], [0, 0, 0, 1]])
    rt_mat_inv = t2.dot(r1).dot(t1)
    return rt_mat.astype(np.float32), rt_mat_inv.astype(np.float32)


# @numba.jit(numba.float32(numba.float32,numba.float32))
def myDot(a, b):
    return np.dot(a, b)


def rotateData(x, y, angle_range=45, specify_angle=None):
    if specify_angle is None:
        angle = np.random.randint(-angle_range, angle_range)
        angle = angle / 180. * np.pi
    else:
        angle = specify_angle
    [image_height, image_width, image_channel] = x.shape
    # move-rotate-move
    [rform, rform_inv] = getRotateMatrix(angle, x.shape)

    # rotate_x = transform.warp(x, rform_inv,
    #                           output_shape=(image_height, image_width))
    rotate_x = cv2.warpPerspective(x, rform, (image_height, image_width))
    rotate_y = y.copy()
    rotate_y[:, :, 2] = 1.
    rotate_y = rotate_y.reshape(image_width * image_height, image_channel)
    # rotate_y = rotate_y.dot(rform.T)
    rotate_y = myDot(rotate_y, rform.T)
    rotate_y = rotate_y.reshape(image_height, image_width, image_channel)
    rotate_y[:, :, 2] = y[:, :, 2]
    # for i in range(image_height):
    #     for j in range(image_width):
    #         rotate_y[i][j][2] = 1.
    #         rotate_y[i][j] = rotate_y[i][j].dot(rform.T)
    #         rotate_y[i][j][2] = y[i][j][2]
    # tex = np.ones((256, 256, 3))
    # from visualize import show
    # show([rotate_y, tex, rotate_x.astype(np.float32)], mode='uvmap')
    return rotate_x, rotate_y


def gaussNoise(x, mean=0, var=0.001):
    noise = np.random.normal(mean, var ** 0.5, x.shape)
    out = x + noise
    out = np.clip(out, 0., 1.0)
    # cv.imshow("gasuss", out)
    return out


def randomErase(x, max_num=4, s_l=0.02, s_h=0.3, r_1=0.3, r_2=1 / 0.3, v_l=0, v_h=1.0):
    [img_h, img_w, img_c] = x.shape
    out = x.copy()
    num = np.random.randint(1, max_num)

    for i in range(num):
        s = np.random.uniform(s_l, s_h) * img_h * img_w
        r = np.random.uniform(r_1, r_2)
        w = int(np.sqrt(s / r))
        h = int(np.sqrt(s * r))
        left = np.random.randint(0, img_w)
        top = np.random.randint(0, img_h)
        if np.random.rand() < 0.25:
            c = np.random.uniform(v_l, v_h)
            out[top:min(top + h, img_h), left:min(left + w, img_w), :] = c
        else:
            # c = np.random.random((min(top + h, img_h) - top, min(left + w, img_w) - left, 3))
            # out[top:min(top + h, img_h), left:min(left + w, img_w), :] = c
            c0 = np.random.uniform(v_l, v_h)
            c1 = np.random.uniform(v_l, v_h)
            c2 = np.random.uniform(v_l, v_h)
            out[top:min(top + h, img_h), left:min(left + w, img_w), :0] = c0
            out[top:min(top + h, img_h), left:min(left + w, img_w), :1] = c1
            out[top:min(top + h, img_h), left:min(left + w, img_w), :2] = c2

    return out


def channelScale(x, min_rate=0.6, max_rate=1.4):
    out = x.copy()
    for i in range(3):
        r = np.random.uniform(min_rate, max_rate)
        out[:, :, i] = out[:, :, i] * r
    return out


# useless
aug_seq = iaa.Sequential([
    iaa.SomeOf((0, 5),
               [
                   # 用高斯模糊，均值模糊，中值模糊中的一种增强。注意OneOf的用法
                   iaa.OneOf([
                       iaa.GaussianBlur((0, 3.0)),
                       iaa.AverageBlur(k=(2, 7)),  # 核大小2~7之间，k=((5, 7), (1, 3))时，核高度5~7，宽度1~3
                       iaa.MedianBlur(k=(3, 11)),
                   ]),

                   # 锐化处理
                   iaa.Sharpen(alpha=(0, 1.0), lightness=(0.75, 1.5)),

                   # 加入高斯噪声
                   iaa.AdditiveGaussianNoise(
                       loc=0, scale=(0.0, 0.05 * 255), per_channel=0.5
                   ),

                   # 5%的概率反转像素的强度，即原来的强度为v那么现在的就是255-v
                   iaa.Invert(0.05, per_channel=True),

                   # 每个像素随机加减-10到10之间的数
                   iaa.Add((-10 / 255., 10 / 255.), per_channel=0.5),

                   # 像素乘上0.5或者1.5之间的数字.
                   iaa.Multiply((0.5, 1.5), per_channel=0.5),

                   # 将整个图像的对比度变为原来的一半或者二倍
                   iaa.ContrastNormalization((0.3, 2.0), per_channel=0.5),

                   # 将RGB变成灰度图然后乘alpha加在原图上
                   iaa.Grayscale(alpha=(0.0, 1.0)),
               ],

               random_order=True  # 随机的顺序把这些操作用在图像上
               )
])


# used in keras version
def prnAugment(x):
    if np.random.rand() > 0.75:
        x = randomErase(x)
    if np.random.rand() > 0.5:
        x = channelScale(x)
    return x


# useless
def unchangeAugment(x):
    if np.random.rand() > 0.5:
        x = randomColor(x)
    if np.random.rand() > 0.5:
        x = randomErase(x)
    if np.random.rand() > 0.5:
        x = aug_seq.augment_image((x * 255.).astype(np.uint8))
    return x


def torchDataAugment(x, y, is_rotate=True):
    if is_rotate:
        if np.random.rand() > 0.75:
            x, y = rotateData(x, y, 90)
    if np.random.rand() > 0.75:
        x = randomErase(x)
    if np.random.rand() > 0.75:
        x = channelScale(x)
    if np.random.rand() > 0.75:
        x = gaussNoise(x)
    return x, y


if __name__ == '__main__':
    import time

    x = io.imread('data/images/AFLW2000-crop/image00004/image00004_cropped.jpg') / 255.
    x = x.astype(np.float32)
    y = np.load('data/images/AFLW2000-crop/image00004/image00004_cropped_uv_posmap.npy')
    y = y.astype(np.float32)

    t1 = time.clock()
    for i in range(1000):
        xr, yr = torchDataAugment(x, y)

    print(time.clock() - t1)
