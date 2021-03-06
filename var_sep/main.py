# Copyright 2020 Jérémie Donà, Jean-Yves Franceschi, Patrick Gallinari, Sylvain Lamprier

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import json
import os
import torch

import numpy as np
import torch.backends.cudnn as cudnn
import torch.optim.lr_scheduler as lr_scheduler

from torch import optim
from torch.utils.data import DataLoader

from var_sep.data.moving_mnist import MovingMNIST
from var_sep.data.chairs import Chairs
from var_sep.data.sst import SST
from var_sep.data.wave_eq import WaveEq, WaveEqPartial
from var_sep.networks.model import SeparableNetwork
from var_sep.networks.factory import get_encoder, get_decoder, get_resnet
from var_sep.networks.utils import ConstantS
from var_sep.options import parser
from var_sep.train import train


if __name__ == "__main__":

    # Arguments
    args = parser.parse_args()

    # CPU / GPU
    os.environ['OMP_NUM_THREADS'] = str(args.num_workers)
    if args.device is None:
        device = torch.device('cpu')
    else:
        cudnn.benchmark = True
        os.environ["CUDA_VISIBLE_DEVICES"] = str(args.device)
        device = torch.device("cuda:0")

    # Seed
    seed = np.random.randint(0, 10000)
    torch.manual_seed(seed)

    # ########
    # DATASETS
    # ########
    last_activation = None
    if args.data == 'mnist':
        train_set = MovingMNIST.make_dataset(args.data_dir, 64, args.nt_cond, args.nt_cond + args.nt_pred, 4, True,
                                             args.n_object, True)
        last_activation = 'sigmoid'
        shape = [1, 64, 64]
    elif args.data == 'chairs':
        train_set = Chairs(True, args.data_dir, args.nt_cond, args.nt_cond + args.nt_pred)
        last_activation = 'sigmoid'
        shape = [3, 64, 64]
    elif args.data == "sst":
        train_set = SST(args.data_dir, args.nt_cond, args.nt_pred, True, zones=args.zones)
        shape = [1, 64, 64]
    elif args.data == "wave":
        train_set = WaveEq(args.data_dir, args.nt_cond, args.nt_cond + args.nt_pred, True, args.downsample)
        last_activation = 'sigmoid'
        shape = [1, 64, 64]
    elif args.data == "wave_partial":
        assert args.architecture not in ['dcgan', 'vgg']
        train_set = WaveEqPartial(args.data_dir, args.nt_cond, args.nt_cond + args.nt_pred, True, args.downsample,
                                  args.n_wave_points)
        last_activation = 'sigmoid'
        shape = [1, args.n_wave_points]

    # Save params
    with open(os.path.join(args.xp_dir, 'params.json'), 'w') as f:
        json.dump(vars(args), f, indent=4, sort_keys=True)

    # ###########
    # DATA LOADER
    # ###########
    def worker_init_fn(worker_id):
        np.random.seed((torch.randint(100000, []).item() + worker_id))
    train_loader = DataLoader(train_set, batch_size=args.batch_size, pin_memory=False, shuffle=True,
                              num_workers=args.num_workers, worker_init_fn=worker_init_fn)

    # ########
    # NETWORKS
    # ########
    if not args.no_s:
        Es = get_encoder(args.architecture, shape, args.code_size_s, args.enc_hidden_size, args.nt_cond,
                         args.init_encoder, args.gain_encoder).to(device)
    else:
        # Es is constant and equal to one
        assert not args.skipco
        args.code_size_s = args.code_size_t
        args.mixing = 'mul'
        Es = ConstantS(return_value=1, code_size=args.code_size_s).to(device)

    Et = get_encoder(args.architecture, shape, args.code_size_t, args.enc_hidden_size, args.nt_cond,
                     args.init_encoder, args.gain_encoder).to(device)

    decoder = get_decoder(args.architecture if args.decoder_architecture is None else args.decoder_architecture,
                          shape, args.code_size_t, args.code_size_s, last_activation, args.dec_hidden_size,
                          args.mixing, args.skipco, args.init_encoder, args.gain_encoder).to(device)

    t_resnet = get_resnet(args.code_size_t, args.n_blocks, args.res_hidden_size, args.init_resnet,
                          args.gain_resnet).to(device)

    sep_net = SeparableNetwork(Es, Et, t_resnet, decoder, args.nt_cond, args.skipco)

    # #########
    # OPTIMIZER
    # #########
    optimizer = optim.Adam(sep_net.parameters(), lr=args.lr, betas=(0.9, args.beta2))
    if args.scheduler is not None:
        scheduler = lr_scheduler.MultiStepLR(optimizer, args.scheduler_milestones, gamma=args.scheduler_decay)
    else:
        scheduler = None

    train(args.xp_dir, train_loader, device, sep_net, optimizer, scheduler, args.apex_amp, args.epochs, args.lamb_ae,
          args.lamb_s, args.lamb_t, args.lamb_pred, args.offset, args.nt_cond, args.nt_pred, args.no_s, args.skipco)
