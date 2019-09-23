import torch
import torch.nn as nn
import torch.nn.functional as F


class Conv2d_BN_AC(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size=3, padding=0, stride=1):
        super(Conv2d_BN_AC, self).__init__()
        self.pipe = nn.Sequential(
            nn.Conv2d(in_channels=in_channels, out_channels=out_channels,
                      kernel_size=kernel_size, stride=stride, padding=padding),
            nn.BatchNorm2d(out_channels),
            nn.ReLU())

    def forward(self, x):
        out = self.pipe(x)
        return out


class ConvTranspose2d_BN_AC(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size=3, padding=0, stride=1):
        super(ConvTranspose2d_BN_AC, self).__init__()
        self.pipe = nn.Sequential(
            nn.ConvTranspose2d(in_channels=in_channels, out_channels=out_channels,
                               kernel_size=kernel_size, stride=stride, padding=padding),
            nn.BatchNorm2d(out_channels),
            nn.ReLU())

    def forward(self, x):
        out = self.pipe(x)
        return out


class PRNResBlock(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size=3, stride=1, with_conv_shortcut=False):
        super(PRNResBlock, self).__init__()
        self.pipe = nn.Sequential(
            Conv2d_BN_AC(in_channels=in_channels, out_channels=int(out_channels / 2), stride=1, kernel_size=1),
            Conv2d_BN_AC(in_channels=int(out_channels) / 2, out_channels=int(out_channels / 2), stride=stride,
                         kernel_size=kernel_size, padding=1),
            nn.Conv2d(in_channels=int(out_channels / 2), out_channels=out_channels, stride=1, kernel_size=1)
        )
        self.shortcut = nn.Sequential()

        if with_conv_shortcut:
            self.shortcut = nn.Sequential(
                nn.Conv2d(in_channels=in_channels, out_channels=out_channels, stride=stride, kernel_size=1))

        self.BN_AC = nn.Sequential(
            nn.BatchNorm2d(out_channels),
            nn.ReLU()
        )

    def forward(self, x):
        out = self.pipe(x)
        out = out + self.shortcut(x)
        out = self.BN_AC(out)
        return out
