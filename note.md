 #PRN:
        Epoch30:    loss: 0.0111 - templateLoss: 0.0547 - mean_absolute_error: 0.0564 - val_loss: 0.0152 - val_templateLoss: 0.0748 - val_mean_absolute_error: 0.0646
        Epoch5:     loss: 0.0386 - templateLoss: 0.1895 - mean_absolute_error: 0.1131 - val_loss: 0.0412 - val_templateLoss: 0.2016 - val_mean_absolute_error: 0.1195


#APRN:
        l=1e-3(encode64)
        Epoch30:    loss: 0.3177 - templateLoss: 0.0444 - mean_absolute_error: 0.0569 - val_loss: 0.1090 - val_templateLoss: 0.0781 - val_mean_absolute_error: 0.0720
        Epoch5:     loss: 1.1835 - templateLoss: 0.1690 - mean_absolute_error: 0.1151 - val_loss: 0.9979 - val_templateLoss: 0.1608 - val_mean_absolute_error: 0.1098



#parameter:
        init:13353618
        initmy:13352633
        prnmy:32127209
###the number of BN parameters are not the same
        Total params: 13,372,445
        Trainable params: 13,360,555
        Non-trainable params: 11,890


$NME_{3Dlandmark}=\frac{1}{N}\sum_{i=0}^{67}(\frac{\|x_{i}-y_{i}\|}{d})$


