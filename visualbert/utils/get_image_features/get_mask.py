#!/usr/bin/env python2

# Copyright (c) 2017-present, Facebook, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##############################################################################

"""Perform inference on a single image or all images with a certain extension
(e.g., .jpg) in a folder.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from collections import defaultdict
import argparse
import cv2  # NOQA (Must import before importing caffe2 due to bug in cv2)
import glob
import logging
import os
import sys
import numpy as np
import base64
import csv
import timeit
import json

import torch

from detectron.utils.io import cache_url
import detectron.utils.c2 as c2_utils


c2_utils.import_detectron_ops()
# OpenCL may be enabled by default in OpenCV3; disable it because it's not
# thread safe and causes unwanted GPU memory allocations.
cv2.ocl.setUseOpenCL(False)

from caffe2.python import workspace
import caffe2

from detectron.core.config import assert_and_infer_cfg
from detectron.core.config import cfg
from detectron.core.config import merge_cfg_from_file
from detectron.utils.timer import Timer
import detectron.core.test_engine as model_engine
import detectron.core.test as infer_engine
import detectron.datasets.dummy_datasets as dummy_datasets
import detectron.utils.c2 as c2_utils
import detectron.utils.logging
import detectron.utils.vis as vis_utils
from detectron.utils.boxes import nms
c2_utils.import_detectron_ops()
# OpenCL may be enabled by default in OpenCV3; disable it because it's not
# thread safe and causes unwanted GPU memory allocations.
cv2.ocl.setUseOpenCL(False)

csv.field_size_limit(sys.maxsize)

BOTTOM_UP_FIELDNAMES = ['image_id', 'image_w', 'image_h', 
                        'num_boxes', 'boxes', 'features']
FIELDNAMES = ['image_id', 'image_w', 'image_h', 'num_boxes', 
            'boxes', 'features', 'object']

from get_mask_utils import detect_from_img, get_model


def parse_args():
    parser = argparse.ArgumentParser(description='End-to-end inference')
    parser.add_argument(
        '--cfg',
        dest='cfg',
        help='cfg model file (/path/to/model_config.yaml)',
        default=None,
        type=str
    )
    parser.add_argument(
        '--wts',
        dest='weights',
        help='weights model file (/path/to/model_weights.pkl)',
        default=None,
        type=str
    )
    parser.add_argument(
        '--output_dir',
        dest='output_dir',
        help='output dir name',
        required=True,
        type=str
    )
    parser.add_argument(
        '--image-ext',
        dest='image_ext',
        help='image file name extension (default: jpg)',
        default='jpg',
        type=str
    )
    parser.add_argument(
        '--bbox_file',
        help="csv file from bottom-up attention model",
        default=None
    )
    parser.add_argument(
        '--total_group',
        help="the number of group for exracting",
        type=int,
        default=1
    )
    parser.add_argument(
        '--group_id',
        help=" group id for current analysis, used to shard",
        type=int,
        default=0
    )
    parser.add_argument(
        '--min_bboxes',
        help=" min number of bboxes",
        type=int,
        default=10
    )
    parser.add_argument(
        '--max_bboxes',
        help=" min number of bboxes",
        type=int,
        default=100
    )
    parser.add_argument(
        '--conf_thresh',
        help=" confidentce",
        type=float,
        default=0.2
    )

    parser.add_argument(
        '--total_split',
        help=" confidentce",
        type=int,
        default=1
    )

    parser.add_argument(
        '--one_giant_file',
        help=" confidentce",
        type=str,
        default=None
    )

    parser.add_argument(
        '--current_split',
        help=" confidentce",
        type=int,
        default=0
    )

    parser.add_argument(
        '--feat_name',
        help=" the name of the feature to extract, default: gpu_0/fc7",
        type=str,
        default="gpu_0/fc7"
    )
    parser.add_argument(
        'im_or_folder', help='image or folder of images', default=None
    )

    parser.add_argument(
        '--no_id',
        action='store_true'
    )

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)
    return parser.parse_args()


def get_detections_from_im(cfg, model, im, image_id, feat_blob_name,
                            MIN_BOXES, MAX_BOXES, conf_thresh=0.2, bboxes=None):

    with c2_utils.NamedCudaScope(0):
        scores, cls_boxes, im_scale = infer_engine.im_detect_bbox(model, 
                                                                im,
                                                                cfg.TEST.SCALE,
                                                                cfg.TEST.MAX_SIZE,
                                                                boxes=bboxes)
        box_features = workspace.FetchBlob(feat_blob_name)
        #print("ss")
        #print(workspace.FetchBlob("gpu_0/fc7"))

        cls_prob = workspace.FetchBlob("gpu_0/cls_prob")
        rois = workspace.FetchBlob("gpu_0/rois")
        max_conf = np.zeros((rois.shape[0]))
        # unscale back to raw image space
        cls_boxes = rois[:, 1:5] / im_scale

        for cls_ind in range(1, cls_prob.shape[1]):
            cls_scores = scores[:, cls_ind]
            dets = np.hstack((cls_boxes, cls_scores[:, np.newaxis])).astype(np.float32)
            keep = np.array(nms(dets, cfg.TEST.NMS))
            max_conf[keep] = np.where(cls_scores[keep] > max_conf[keep], cls_scores[keep], max_conf[keep])

        keep_boxes = np.where(max_conf >= conf_thresh)[0]
        if len(keep_boxes) < MIN_BOXES:
            keep_boxes = np.argsort(max_conf)[::-1][:MIN_BOXES]
        elif len(keep_boxes) > MAX_BOXES:
            keep_boxes = np.argsort(max_conf)[::-1][:MAX_BOXES]
        objects = np.argmax(cls_prob[keep_boxes], axis=1)



        #print(cls_boxes[keep_boxes])
        #print("keep_boxes", keep_boxes)
        #print("max_conf", max_conf)
        #print("cls_boxes", cls_boxes[0])
        #print("im_h", im.shape[0])

    return box_features[keep_boxes], max_conf[keep_boxes], cls_boxes[keep_boxes]

    #return {
    #    "image_id": image_id,
    #    "image_h": np.size(im, 0),
    #    "image_w": np.size(im, 1),
    #    'num_boxes': len(keep_boxes),
    #    'boxes': base64.b64encode(cls_boxes[keep_boxes]),
    #    'features': base64.b64encode(box_features[keep_boxes]),
    #    'object': base64.b64encode(objects)
    #}


def extract_bboxes(bottom_up_csv_file):
    image_bboxes = {}

    with open(bottom_up_csv_file, "r") as tsv_in_file:
        reader = csv.DictReader(tsv_in_file, delimiter='\t', 
                                fieldnames=BOTTOM_UP_FIELDNAMES)
        for item in reader:
            item['num_boxes'] = int(item['num_boxes'])
            image_id = int(item['image_id'])
            image_w = float(item['image_w'])
            image_h = float(item['image_h'])

            bbox = np.frombuffer(
                base64.b64decode(item['boxes']),
                dtype=np.float32).reshape((item['num_boxes'], -1))

            image_bboxes[image_id] = bbox
    return image_bboxes


import os
def recurse_find_image(folder, image_list, image_ext):
    files = os.listdir(folder)
    files.sort()
    for i in files:
        path = os.path.join(folder, i)
        if os.path.isdir(path):
            recurse_find_image(path, image_list, image_ext)
        elif path.endswith(image_ext):
            image_list.append(path)


def main(args):
    logger = logging.getLogger(__name__)

    model = get_model()
    start = timeit.default_timer()

    im_list = []
    recurse_find_image(args.im_or_folder, im_list, args.image_ext)
    print(im_list[:10])

    print(f"There are {len(im_list)} images to cache in total.")

    if args.total_split != 1:
        im_lists = np.array_split(im_list, args.total_split)
        im_list= im_lists[args.current_split]
        print(
            f"Split {args.current_split}: There are currently {len(im_list)} images to cache."
        )

    '''if os.path.isdir(args.im_or_folder):
        im_list = glob.iglob(args.im_or_folder + '/*.' + args.image_ext)
    else:
        im_list = [args.im_or_folder]'''

    image_bboxes = {} if args.bbox_file is None else extract_bboxes(args.bbox_file)
    count = 0
    if not os.path.exists(args.output_dir):
        os.makedirs(args.output_dir)

    one_giant_file = args.one_giant_file
    if one_giant_file is not None:
        giant_file = {}

    for im_name in im_list:
        im_base_name = os.path.basename(im_name)
        image_id = (
            int(im_base_name.split(".")[0].split("_")[-1])
            if not args.no_id
            else None
        )
        if not args.no_id:
            '''if image_id % args.total_group == args.group_id:
                if not args.no_id:
                    bbox = image_bboxes[image_id] if image_id in image_bboxes else None
                else:
                    bbox = None
                im = cv2.imread(im_name)
                if im is not None:
                    outfile = os.path.join(args.output_dir, 
                                        im_base_name.replace('jpg', 'npy'))
                    lock_folder = outfile.replace('npy', 'lock')
                    if not os.path.exists(lock_folder) and os.path.exists(outfile):
                        continue
                    if not os.path.exists(lock_folder):
                        os.makedirs(lock_folder)

                    result = get_detections_from_im(cfg, model, im, 
                                                    image_id,args.feat_name,
                                                    args.min_bboxes, 
                                                    args.max_bboxes, 
                                                    bboxes=bbox)
                    np.save(outfile, result)
                    os.rmdir(lock_folder)

                    second_result = np.load(outfile)
                    print(result[1])
                    print(second_result[1])

                count += 1

                if count % 100 == 0:
                    end = timeit.default_timer()
                    epoch_time = end - start
                    print('process {:d} images after {:.1f} s'.format(count, epoch_time))'''
            assert(0)

        else:
            bbox = None
            im = cv2.imread(im_name)
            if im is not None:
                outfile = f"{os.path.join(args.output_dir, im_base_name)}.npz"
                lock_folder = f'{outfile}.lock'
                if not os.path.exists(lock_folder) and os.path.exists(outfile):
                    continue
                if not os.path.exists(lock_folder):
                    os.makedirs(lock_folder)

                detection = detect_from_img(model, im)

                #for i in detection:
                #    detection[i] = numpy.array(detection[i])
                if one_giant_file is not None:

                    #box_features = torch.Tensor(box_features)
                    #cls_boxes = torch.Tensor(cls_boxes)
                    #max_conf = torch.Tensor(max_conf)

                    giant_file[im_base_name] = detection

                #np.savez(outfile, box_features=box_features, max_conf=max_conf, cls_boxes=cls_boxes)
                os.rmdir(lock_folder)

            count += 1

            if count % 100 == 0:
                end = timeit.default_timer()
                epoch_time = end - start
                print('process {:d} images after {:.1f} s'.format(count, epoch_time))

    if one_giant_file is not None:
        torch.save(giant_file, one_giant_file)

if __name__ == '__main__':
    workspace.GlobalInit(['caffe2', '--caffe2_log_level=0'])
    detectron.utils.logging.setup_logging(__name__)
    args = parse_args()
    if args.group_id >= args.total_group:
        exit("sharding group %d is greater than the total group %d" %(args.group_id, args.total_group ))

    main(args)
