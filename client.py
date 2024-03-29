import json
from copy import deepcopy
from random import randrange

import numpy as np
from torch.utils.data import DataLoader, sampler
import torch

import datasets
from server import Server


class SecAggregator:
    def __init__(self, common_base, common_mod):
        # 从0-common_mod-1选择一个随机数作为私钥
        self.secretkey = randrange(common_mod)
        if self.secretkey == 0:
            self.secretkey = 1
        # 群的生成元
        self.base = common_base
        # 群的模数
        self.mod = common_mod
        # 由私钥生成公钥，离散大数问题，由公钥难解私钥
        self.pubkey = (self.base ** self.secretkey) % self.mod
        # 自己的随机数密钥bu
        self.sndkey = randrange(common_mod)
        # 其他客户端的公钥
        self.keys = {}
        self.id = ''

    def public_key(self):
        return self.pubkey

    def set_weights(self, wghts, dims):
        # 模型参数
        self.weights = wghts
        # 维度
        self.dim = dims

    def configure(self, base, mod):
        # 生成密钥的乘法循环群
        # 生成元
        self.base = base
        # 模数
        self.mod = mod
        # 由私钥生成公钥
        self.pubkey = (self.base ** self.secretkey) % self.mod

    # 生成噪声张量（加噪声） PRG伪随机生成器，seed一样，随机向量也一样
    # def generate_weights(self, seed):
    #     # 定义随机数种子
    #     np.random.seed(seed)
    #     # 生成dim维度的向量
    #     if len(self.dim) == 0:
    #         if self.weights.dtype == np.int64:
    #             return np.int64(np.random.rand())
    #         elif self.weights.dtype == np.float32:
    #             return np.int64(np.random.rand())
    #     elif len(self.dim) == 1:
    #         if self.weights.dtype == np.int64:
    #             return np.int64(np.random.rand(self.dim[0]))
    #         elif self.weights.dtype == np.float32:
    #             return np.float32(np.random.rand(self.dim[0]))
    #     elif len(self.dim) == 2:
    #         if self.weights.dtype == np.int64:
    #             return np.int64(np.random.rand(self.dim[0], self.dim[1]))
    #         elif self.weights.dtype == np.float32:
    #             return np.float32(np.random.rand(self.dim[0], self.dim[1]))
    #     elif len(self.dim) == 3:
    #         if self.weights.dtype == np.int64:
    #             return np.int64(np.random.rand(self.dim[0], self.dim[1], self.dim[2]))
    #         elif self.weights.dtype == np.float32:
    #             return np.float32(np.random.rand(self.dim[0], self.dim[1], self.dim[2]))
    #     elif len(self.dim) == 4:
    #         if self.weights.dtype == np.int64:
    #             return np.int64(np.random.rand(self.dim[0], self.dim[1], self.dim[2], self.dim[3]))
    #         elif self.weights.dtype == np.float32:
    #             return np.float32(np.random.rand(self.dim[0], self.dim[1], self.dim[2], self.dim[3]))

    # 生成加入掩码之后的参数

    def generate_weights(self, seed):
        torch.manual_seed(seed)
        if len(self.dim) == 0:
            return torch.tensor(0)
        elif len(self.dim) == 1:
            if self.weights.dtype == torch.int64:
                return torch.randn((self.dim[0]))
            elif self.weights.dtype == torch.float32:
                return torch.randn((self.dim[0]), dtype=torch.float32)
        elif len(self.dim) == 2:
            if self.weights.dtype == torch.int64:
                return torch.randn((self.dim[0], self.dim[1]))
            elif self.weights.dtype == torch.float32:
                return torch.randn((self.dim[0], self.dim[1]), dtype=torch.float32)
        elif len(self.dim) == 3:
            if self.weights.dtype == torch.int64:
                return torch.randn((self.dim[0], self.dim[1], self.dim[2]))
            elif self.weights.dtype == torch.float32:
                return torch.randn((self.dim[0], self.dim[1], self.dim[2]), dtype=torch.float32)
        elif len(self.dim) == 4:
            if self.weights.dtype == torch.int64:
                return torch.randn((self.dim[0], self.dim[1], self.dim[2], self.dim[3]))
            elif self.weights.dtype == torch.float32:
                return torch.randn((self.dim[0], self.dim[1], self.dim[2], self.dim[3]), dtype=torch.float32)

    def prepare_weights(self, shared_keys, myid):
        # 其他客户端的公钥
        self.keys = shared_keys
        self.id = myid
        wghts = deepcopy(self.weights)
        for sid in shared_keys:
            # 加掩码
            if sid > myid:
                # shared_keys[sid] ** self.secretkey 生成公共密钥
                wghts += self.generate_weights((shared_keys[sid] ** self.secretkey) % self.mod)
            elif sid < myid:
                wghts -= self.generate_weights((shared_keys[sid] ** self.secretkey) % self.mod)
        # 加自己的掩码bu
        wghts += self.generate_weights(self.sndkey)
        return wghts

    def private_secret(self):
        return self.generate_weights(self.sndkey)


class Client(object):

    def __init__(self, conf, model, train_dataset, id=-1):
        self.client_id = id

        # 安全聚合
        self.sec_agg = SecAggregator(2, 17)
        # 最小生成树结构
        self.part_connect_graph = []
        # 客户端列表
        self.client_dict = {}
        self.client_list = []
        # 参与训练的客户端私钥和bu的份额
        self.client_shared_key_bu = {}
        # 存储其他客户端的公钥
        self.client_pubkey = {self.client_id: self.sec_agg.pubkey}

        self.conf = conf
        # 客户端本地模型(一般由服务器传输)
        self.local_model = model

        self.train_dataset = train_dataset

        # 按ID对训练集合的拆分
        all_range = list(range(len(self.train_dataset)))
        data_len = int(len(self.train_dataset) / self.conf['no_models'])
        train_indices = all_range[int(id) * data_len: (int(id) + 1) * data_len]

        self.train_loader = DataLoader(self.train_dataset, batch_size=conf["batch_size"],
                                       sampler=sampler.SubsetRandomSampler(train_indices))

    # t-out-of-n
    def t_out_of_n(self, t, n, k):
        params = []
        for i in range(t - 1):
            a = randrange(self.sec_agg.mod)
            params.append(a)
        part_key = {}
        for i in range(n):
            key = k
            for j in range(t - 1):
                key += params[j] * (i + 1) ** (j + 1)
            # key = key % self.sec_agg.mod
            part_key[self.client_list[i].client_id] = key
        return part_key

    # 存储来自其他客户端的份额
    def store_shared_secretkey_bu(self, part_msg):
        for origin_id in part_msg:
            if origin_id == self.client_id:
                break
            for client_id in part_msg[origin_id]:
                if client_id == self.client_id:
                    self.client_shared_key_bu[origin_id] = part_msg[origin_id][client_id]
            break
        # 转发
        # for origin_id in part_msg:
        #     self.transmit_part_secretkey_bu_to_adj(part_msg, last_id)
        #     break

    def store_pubkey(self, pubkey):
        client_id = list(pubkey.keys())[0]
        self.client_pubkey[client_id] = pubkey[client_id]

    # 分享私钥和bu
    def shared_secretkey_bu(self):
        part_secretkey = self.t_out_of_n(self.conf["t"], self.conf["k"], self.sec_agg.secretkey)
        part_bu = self.t_out_of_n(self.conf["t"], self.conf["k"], self.sec_agg.sndkey)
        part_secretkey_bu = {}
        for client_id in part_secretkey:
            part_secretkey_bu[client_id] = []
            part_secretkey_bu[client_id].append(part_secretkey[client_id])
            part_secretkey_bu[client_id].append(part_bu[client_id])
        self.client_shared_key_bu[self.client_id] = part_secretkey_bu[self.client_id]
        # self.send_part_secretkey_bu_to_adj(part_secretkey_bu)
        return {self.client_id: part_secretkey_bu}

    def mask(self, diff):
        shared_keys = {}
        for client1, client2, cost in self.part_connect_graph:
            if client1 == self.client_id:
                shared_keys[client2] = self.client_pubkey[client2]
            if client2 == self.client_id:
                shared_keys[client1] = self.client_pubkey[client1]
        # for name in diff:
        #     # item = diff[name].detach().numpy()
        #     item = diff[name]
        #     _item = item.detach().numpy()
        #     dim = _item.shape
        #     self.sec_agg.set_weights(_item, dim)
        #     _item = self.sec_agg.prepare_weights(shared_keys, self.client_id)
        #     diff[name] = torch.tensor(_item)
        for name in diff:
            item = diff[name]
            dim = item.shape
            self.sec_agg.set_weights(item, dim)
            _item = self.sec_agg.prepare_weights(shared_keys, self.client_id)
            diff[name] = _item

    # 计算时延
    def compute_communication_cost(self):
        return []

    # 本地模型训练函数：采用 交叉熵 作为本地训练的损失函数，并使用 梯度下降 来求解参数
    def local_train(self, model):
        # 整体的过程：拉取服务器的模型，通过部分本地数据集训练得到
        for name, param in model.state_dict().items():
            # 客户端首先用服务器端下发的全局模型覆盖本地模型
            self.local_model.state_dict()[name].copy_(param.clone())

        optimizer = torch.optim.SGD(self.local_model.parameters(), lr=self.conf['lr'], momentum=self.conf['momentum'])
        self.local_model.train()
        for e in range(self.conf["local_epochs"]):
            # print(1)
            for batch_id, batch in enumerate(self.train_loader):
                data, target = batch

                if torch.cuda.is_available():
                    data = data.cuda()
                    target = target.cuda()

                # 更新梯度
                optimizer.zero_grad()
                # 前向传播
                output = self.local_model(data)
                # 计算损失值
                loss = torch.nn.functional.cross_entropy(output, target)
                # 反向传播
                loss.backward()
                # 更新参数
                optimizer.step()
            # print(2)
            print("Client {} Epoch {} done.".format(self.client_id, e))

        diff = dict()
        for name, data in self.local_model.state_dict().items():
            # 计算训练后与训练前的差值
            diff[name] = (data - model.state_dict()[name])

        # 加掩码
        # self.mask(diff)

        return diff



if __name__ == '__main__':

    with open("./utils/conf.json", 'r') as f:
        conf = json.load(f)
    _, eval_datasets = datasets.get_dataset("./data/", conf["type"])
    server = Server(conf, eval_datasets)
    for name, params in server.global_model.state_dict().items():
        print(name)
        print(params)
    acc, loss = server.model_eval()
    print("Global Epoch {}, acc: {}, loss: {}\n".format(0, acc, loss))
