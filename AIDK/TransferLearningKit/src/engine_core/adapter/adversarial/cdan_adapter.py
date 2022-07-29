#!/usr/bin/python
# -*- coding: utf-8 -*-
from .adversarial_adapter import AdversarialAdapter
import torch.nn as nn
import torch
import math
from .grl import GradientReverseLayer
import logging

class RandomLayer(nn.Module):
    ''' A non-trainable random layer, see paper: Conditional Adversarial Domain Adaptation

    '''
    def __init__(self, input_dim_list, output_dim):
        ''' init method

        :param input_dim_list: input dim list
        :param output_dim: output dim
        '''
        super(RandomLayer, self).__init__()
        self.input_num = len(input_dim_list)
        self.output_dim = output_dim
        self.random_matrix = [torch.randn(input_dim_list[i], output_dim)  # random matrix (no trainable)
                              for i in range(self.input_num)]

    def forward(self, input_list):
        ''' forward

        :param input_list: input tensor list, must match 'input_dim_list'
        :return:
        '''
        return_list = [torch.mm(input_list[i], self.random_matrix[i]) for i in range(self.input_num)]
        return_tensor = return_list[0] / math.pow(float(self.output_dim), 1.0 / len(return_list))
        for single in return_list[1:]:
            return_tensor = torch.mul(return_tensor, single)
        return return_tensor

    def cuda(self):
        ''' move random matrix to cuda

        :return:
        '''
        super(RandomLayer, self).cuda()
        self.random_matrix = [val.cuda() for val in self.random_matrix]

class CDANAdapter(AdversarialAdapter):
    ''' CDAN , see paper: Conditional Adversarial Domain Adaptation

    '''
    def __init__(self,in_feature, hidden_size,dropout_rate,grl_coeff_alpha,grl_coeff_high,max_iter,
                 backbone_output_size,enable_random_layer,enable_entropy_weight):
        ''' Init method

        :param in_feature: adversarial network input
        :param hidden_size: hidden size
        :param dropout_rate: dropout rate
        :param grl_coeff_alpha: GradientReverseLayer alpha argument to calculate coef
        :param grl_coeff_high: GradientReverseLayer coef high
        :param max_iter: max iter for one epoch
        :param backbone_output_size: backbone output size
        :param enable_random_layer: whether using random layer
        :param enable_entropy_weight: whether using entropy as weight
        '''
        super(CDANAdapter, self).__init__(in_feature, hidden_size,dropout_rate,grl_coeff_alpha,grl_coeff_high,max_iter)
        if enable_random_layer:
            self._random_layer = RandomLayer([in_feature, backbone_output_size], in_feature)
            logging.info("CDAN enable random layer")
        else:
            self._random_layer = None
        self.entropy_grl = GradientReverseLayer(coeff_alpha=grl_coeff_alpha, coeff_high=grl_coeff_high,
                                        max_iter=max_iter, enable_step=self.training)

        self._enable_entropy_weight = enable_entropy_weight
        if self._enable_entropy_weight:
            logging.info("CDAN enable entropy weight")

    def forward(self, x,**kwargs):
        ''' CDAN forward

        :param args: args
        :param kwargs: kwargs, must contain backbone_output
        :return:
        '''
        adapter_input = x
        backbone_output = kwargs['backbone_output']

        if self._random_layer is None:
            op_out = torch.bmm(backbone_output.unsqueeze(2), adapter_input.unsqueeze(1))  # outer product of feature and output
            return super(CDANAdapter, self).forward(op_out.view(-1, backbone_output.size(1) * adapter_input.size(1)))
        else:  # random layer for computation efficiency
            random_out = self._random_layer.forward([adapter_input,backbone_output])  # fixed random layer
            return super(CDANAdapter, self).forward(random_out.view(-1, random_out.size(1)))

    def loss(self,output_prob,label,**kwargs):
        ''' adapter loss function

        :param output_prob: output probability
        :param label: ground truth
        :param kwargs: kwargs, must contain backbone_output
        :return: loss
        '''
        backbone_output = kwargs['backbone_output']
        backbone_prediction = nn.Softmax(dim=1)(backbone_output)

        if self._enable_entropy_weight:  # quantify the uncertainty of classifier predictions by the entropy, to emphasize those easy-to-transfer exmples
            entropy = torch.mean(torch.special.entr(backbone_prediction), dim=1)
            entropy = self.entropy_grl(entropy)
            entropy = 1.0 + torch.exp(-entropy)               # entropy as weight
            ########### weight normalization ###########
            sum_weight = torch.sum(entropy).detach().item()                # simplify gradient
            return torch.sum(entropy * nn.BCELoss(reduction='none')(output_prob, label))/sum_weight
        else:
            return nn.BCELoss()(output_prob, label)