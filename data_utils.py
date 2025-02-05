# @Time    : 2023/1/22 16:22
# @Author  : tk
# @FileName: data_utils.py


import copy
import json
import os
import random
import typing
from enum import Enum
import numpy as np
import torch
from deep_training.data_helper import DataHelper, ModelArguments, TrainingArguments, DataArguments
from fastdatasets.record import load_dataset as Loader, RECORD, WriterObject, gfile
from tqdm import tqdm
from transformers import HfArgumentParser,PreTrainedTokenizer
from models import MyTransformer,MossConfig,MossTokenizer,LoraArguments,PromptArguments

from data_processer import DataStrategy, TokenSupervision, TokenUnSupervision, TokenSupervisionRounds, \
    TokenRoundsForMoss

lora_info_args = {
    'with_lora': True,  # 是否启用lora模块
    'r': 8,
    'target_modules': ['qkv_proj'],
    'target_dtype': 16, # 半精度
    'lora_alpha': 32,
    'lora_dropout': 0.1,
    'bias': 'none',  # Bias type for Lora. Can be 'none', 'all' or 'lora_only'"
    'modules_to_save' : None, # "help": "List of modules apart from LoRA layers to be set as trainable and saved in the final checkpoint. "
}

adalora_info_args = {
    'with_lora': False,  # 是否启用adalora模块
    'r': 8,
    'target_modules': ['qkv_proj'],
    'target_dtype': 16, # 半精度
    'lora_alpha': 32,
    'lora_dropout': 0.1,
    'bias': 'none',  # Bias type for Lora. Can be 'none', 'all' or 'lora_only'"
    'modules_to_save' : None, # "help": "List of modules apart from LoRA layers to be set as trainable and saved in the final checkpoint. "

    'target_r':8, # Target Lora matrix dimension.
    'init_r': 12, #Intial Lora matrix dimension.
    'tinit': 0, #The steps of initial warmup.
    'tfinal': 0, #The steps of final warmup.
    'deltaT': 1, #Step interval of rank allocation.
    'beta1': 0.85, #Hyperparameter of EMA.
    'beta2': 0.85, #Hyperparameter of EMA.
    'orth_reg_weight': 0.5, #The orthogonal regularization coefficient.
    'total_step': None, #The total training steps.
    'rank_pattern': None, #The saved rank pattern.
}

prompt_info_args = {
    "with_prompt": False,
    "prompt_type": "prefix_tuning", # one of prompt_tuning,p_tuning,prefix_tuning,adaption_prompt
    "task_type": "causal_lm", #  one of seq_cls,seq_2_seq_lm,causal_lm,token_cls
    "prefix_projection": False, # Whether to project the prefix tokens"
    "num_virtual_tokens": 16, # Number of virtual tokens
    # "token_dim": 2048, # The hidden embedding dimension of the base transformer model.
    # "num_transformer_submodules": 1, # The number of transformer submodules in the base transformer model.
    # "num_attention_heads" : 24, # The number of attention heads in the base transformer model.
    # "num_layers": 1, # The number of layers in the base transformer model.
    # "encoder_hidden_size": 2048, # The hidden size of the encoder
    # "prefix_projection": False # Whether to project the prefix tokens"
}

train_info_args = {
    'devices': 1,
    'data_backend': 'record',  #one of record lmdb, 超大数据集可以使用 lmdb , 注 lmdb 存储空间比record大
    'model_type': 'moss',
    # 预训练模型路径 , 从0训练，则置空
    'model_name_or_path': '/data/nlp/pre_models/torch/moss/moss-moon-003-sft',
    'config_name': '/data/nlp/pre_models/torch/moss/moss-moon-003-sft/config.json',
    'tokenizer_name': '/data/nlp/pre_models/torch/moss/moss-moon-003-sft',

    # 'model_name_or_path': '/data/nlp/pre_models/torch/moss/moss-moon-003-sft-int4',
    # 'config_name': '/data/nlp/pre_models/torch/moss/moss-moon-003-sft-int4/config.json',
    # 'tokenizer_name': '/data/nlp/pre_models/torch/moss/moss-moon-003-sft-int4',

    'convert_onnx': False, # 转换onnx模型
    'do_train': True,
    'train_file':  [ './data/train.json'],
    'max_epochs': 20,
    'max_steps': -1,
    'optimizer': 'lion', # one of adamw,adam,lamb,lion

    'scheduler_type': 'CAWR',
    'scheduler':{'T_mult': 1, 'rewarm_epoch_num': 0.5, 'verbose': False},

    # 'scheduler_type': 'linear',# one of [linear,WarmupCosine,CAWR,CAL,Step,ReduceLROnPlateau
    # 'scheduler': None,

    # 切换scheduler类型
    # 'scheduler_type': 'WarmupCosine',
    # 'scheduler': None,

    # 'scheduler_type': 'ReduceLROnPlateau',
    # 'scheduler': None,

    # 'scheduler_type': 'Step',
    # 'scheduler':{ 'decay_rate': 0.999,'decay_steps': 100,'verbose': True},

    # 'scheduler_type': 'CAWR',
    # 'scheduler':{'T_mult': 1, 'rewarm_epoch_num': 2, 'verbose': True},

    # 'scheduler_type': 'CAL',
    # 'scheduler': {'rewarm_epoch_num': 2,'verbose': True},


    'optimizer_betas': (0.9, 0.999),
    'train_batch_size': 2,
    'eval_batch_size': 2,
    'test_batch_size': 2,
    'learning_rate': 2e-5,  #
    'adam_epsilon': 1e-8,
    'gradient_accumulation_steps': 1,
    'max_grad_norm': 1.0,
    'weight_decay': 0,
    'warmup_steps': 0,
    'output_dir': './output',
    'max_seq_length': 1024, # 如果资源充足，推荐长度2048 与官方保持一致
    'max_target_length': 100,  # 预测最大长度, 保留字段
    'use_fast_tokenizer': False,
    'do_lower_case': False,

    ##############  lora模块
    #注意lora,adalora 和 ptuning-v2 禁止同时使用
   'lora': {**lora_info_args},
   'adalora': {**adalora_info_args},
   'prompt': {**prompt_info_args}
}

#lora 模式暂时不支持deepspeed
enable_deepspeed = False


data_conf = {
    'strategy': DataStrategy.mos_rounds,  # 数据策略选项
    DataStrategy.sup: {
        'stride':  int(train_info_args['max_seq_length'] / 3 * 2),
    },
    DataStrategy.unsup: {
        'stride':  int(train_info_args['max_seq_length'] / 3 * 2),
    },
    DataStrategy.sub_rounds: {
        'stride': int(train_info_args['max_seq_length'] / 3 * 2),
    },
    DataStrategy.mos_rounds: {
    }
}

def get_deepspeed_config():
    # 是否开启deepspeed
    if not enable_deepspeed:
        return None
    with open('./deepspeed.json', mode='r', encoding='utf-8') as f:
        deepspeed_config = json.loads(f.read())
    return deepspeed_config


class NN_DataHelper(DataHelper):
    index = 1
    def on_data_ready(self):
        self.index = -1

    # 切分词
    def on_data_process(self, data: typing.Any, mode: str):
        self.index += 1

        tokenizer: MossTokenizer
        config: MossConfig
        max_seq_length = self.max_seq_length_dict[mode]
        tokenizer = self.tokenizer
        config = self.config
        examples = data

        strategy = data_conf['strategy']
        if strategy == DataStrategy.sup:
            ds = TokenSupervision.process(tokenizer, config=config, max_seq_length=max_seq_length, examples=examples,
                                          **data_conf[strategy])
        elif strategy == DataStrategy.unsup:
            ds = TokenUnSupervision.process(tokenizer, config=config, max_seq_length=max_seq_length, examples=examples,
                                            **data_conf[strategy])
        elif strategy == DataStrategy.sub_rounds:
            ds = TokenSupervisionRounds.process(tokenizer, config=config, max_seq_length=max_seq_length,
                                                examples=examples,
                                                **data_conf[strategy])
        elif strategy == DataStrategy.mos_rounds:
            ds = TokenRoundsForMoss.process(tokenizer, config=config, max_seq_length=max_seq_length,
                                                examples=examples,
                                                **data_conf[strategy])
        else:
            raise ValueError('Invalid strategy', strategy)
        if not ds:
            return None

        if self.index < 3:
            print(ds[0])
        return ds

    # {
    #     "id": 0, "paragraph": [
    #     # 一轮会话
    #     {
    #         "q": "从南京到上海的路线",
    #         "a": [
    #             "你好，南京到上海的路线如下：",
    #             "1. 南京到上海，可以乘坐南京地铁1号线，在南京站乘坐轨道交通1号线。",
    #             "2. 南京到浦东机场，可以搭乘上海地铁1号，在陆家嘴站乘坐地铁1线，在浦东国际机场站乘坐机场快线，前往上海浦东国际机场。",
    #             "3. 上海到南京，可以换乘上海地铁2号线，从南京站换乘地铁2线，再从南京南站换乘地铁1路，然后到达上海站"
    #         ]
    #     }
    #     # 二轮....
    # ]
    # }

    # 读取文件
    def on_get_corpus(self, files: typing.List, mode: str):
        D = []
        strategy = data_conf['strategy']
        for file in files:
            with open(file, mode='r', encoding='utf-8', newline='\n') as f:
                lines = f.readlines()

            for i, line in enumerate(lines):
                jd = json.loads(line)
                if not jd:
                    continue
                paragraph = jd['paragraph']
                if i < 10:
                    print(paragraph)
                sub = []
                # 自行做模板
                for session in paragraph:
                    a = session['a']
                    assert len(a), ValueError('answer cannot empty')
                    sub.append(session)
                if strategy == DataStrategy.mos_rounds:
                   D.append((jd['meta_instruction'],copy.deepcopy(sub)))
                else:
                    D.append(copy.deepcopy(sub))
                sub.clear()

        return D

    def collate_fn(self, batch):
        o = {}
        for i, b in enumerate(batch):
            if i == 0:
                for k in b:
                    o[k] = [torch.tensor(b[k])]
            else:
                for k in b:
                    o[k].append(torch.tensor(b[k]))
        for k in o:
            o[k] = torch.stack(o[k])

        maxlen = torch.max(o.pop('seqlen'))
        o['input_ids'] = o['input_ids'][:, :maxlen]
        o['attention_mask'] = o['attention_mask'][:, :maxlen]
        o['labels'] = o['labels'][:, :maxlen].long()
        return o


if __name__ == '__main__':
    parser = HfArgumentParser((ModelArguments, TrainingArguments, DataArguments, LoraArguments,PromptArguments))
    model_args, training_args, data_args, _,_ = parser.parse_dict(train_info_args)

    dataHelper = NN_DataHelper(model_args, training_args, data_args)
    tokenizer, config, _,_ = dataHelper.load_tokenizer_and_config(tokenizer_class_name=MossTokenizer,config_class_name=MossConfig)
    config.torch_dtype = "float16"

    # 检测是否存在 output/dataset_0-train.record ，不存在则制作数据集
    if data_args.do_train:
        dataHelper.make_dataset_with_args(data_args.train_file,mixed_data=False,shuffle=True,mode='train')
    if data_args.do_eval:
        dataHelper.make_dataset_with_args(data_args.eval_file, shuffle=False,mode='eval')
    if data_args.do_test:
        dataHelper.make_dataset_with_args(data_args.test_file, shuffle=False,mode='test')


    # def shuffle_records(record_filenames, outfile, compression_type='GZIP'):
    #     print('shuffle_records record...')
    #     options = RECORD.TFRecordOptions(compression_type=compression_type)
    #     dataset_reader = Loader.RandomDataset(record_filenames, options=options, with_share_memory=True)
    #     data_size = len(dataset_reader)
    #     all_example = []
    #     for i in tqdm(range(data_size), desc='load records'):
    #         serialized = dataset_reader[i]
    #         all_example.append(serialized)
    #     dataset_reader.close()
    #
    #     shuffle_idx = list(range(data_size))
    #     random.shuffle(shuffle_idx)
    #     writer = WriterObject(outfile, options=options)
    #     for i in tqdm(shuffle_idx, desc='shuffle record'):
    #         example = all_example[i]
    #         writer.write(example)
    #     writer.close()
    #
    #
    # # 对每个record 再次打乱
    # for filename in dataHelper.train_files:
    #     shuffle_records(filename, filename)
