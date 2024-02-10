import argparse
import logging
import os

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from torchvision import transforms
from torchvision.utils import save_image, make_grid
from pathlib import Path

from utils.data_loading import BasicDataset, CarvanaDataset
from unet import UNet
from utils.utils import plot_img_and_mask
from torch.utils.data import DataLoader, random_split, Subset 
from evaluate import evaluate
import csv

dir_img = Path('./data/imgs/')
dir_mask = Path('./data/masks/')
dir_train_img = Path('./data/train/imgs/')
dir_train_mask = Path('./data/train/masks/')
dir_val_img = Path('./data/val/imgs/')
dir_val_mask = Path('./data/val/masks/')
dir_test_img = Path('./data/test/imgs/')
dir_test_mask = Path('./data/test/masks/')
full_img_size = (140, 175)

def qualitative_results(dataset, path, model, device='cpu'):
    len_dataset = len(dataset)
    dataset = Subset(dataset, range(0, len_dataset, len_dataset//6))
    row_n = len_dataset//6 + 1
    dataset_loader = DataLoader(dataset, batch_size= row_n, shuffle=False)

    batch = next(iter(dataset_loader))
    images, true_masks = batch['image'], batch['mask']
    images = images.to(device=device)
    true_masks = true_masks.to(device=device).unsqueeze(1).float()
    
    out_masks = model(images)
    out_masks = out_masks.argmax(dim=1).unsqueeze(1).float()

    images = F.interpolate(images, (full_img_size[1], full_img_size[0]), mode='bilinear')
    true_masks = F.interpolate(true_masks, (full_img_size[1], full_img_size[0]), mode='bilinear')
    out_masks = F.interpolate(out_masks, (full_img_size[1], full_img_size[0]), mode='bilinear')
    
    save_image(make_grid(images, nrow=row_n), os.path.join(path, 'images.png'))
    save_image(make_grid(true_masks, nrow=row_n), os.path.join(path,'true_masks.png'))
    save_image(make_grid(out_masks, nrow=row_n), os.path.join(path,'out_masks.png'))
    return

def get_args():
    parser = argparse.ArgumentParser(description='Predict masks from input images')
    parser.add_argument('--model', '-m', default='MODEL.pth', metavar='FILE',
                        help='Specify the file in which the model is stored')
    parser.add_argument('--amp', action='store_true', default=False, help='Use mixed precision')
    parser.add_argument('--bilinear', action='store_true', default=False, help='Use bilinear upsampling')
    parser.add_argument('--classes', '-c', type=int, default=2, help='Number of classes')
    parser.add_argument('--default', action='store_true', default=False, help='Save checkpoints')
    return parser.parse_args()

if __name__ == '__main__':
    args = get_args()

    net = UNet(n_channels=1, n_classes=args.classes, bilinear=args.bilinear)
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    net.to(device=device)
    state_dict = torch.load(args.model, map_location=device)
    mask_values = state_dict.pop('mask_values', [0, 1])
    net.load_state_dict(state_dict)
    net.eval()

    img_scale = 0.5
    val_percent = 0.1
    batch_size = 32

    if args.default:
        try:
            dataset = CarvanaDataset(dir_img, dir_mask, img_scale)
        except (AssertionError, RuntimeError, IndexError):
            dataset = BasicDataset(dir_img, dir_mask, img_scale)

        # 2. Split into train / validation partitions
        n_val = int(len(dataset) * val_percent)
        n_train = len(dataset) - n_val
        train_set, val_set = random_split(dataset, [n_train, n_val], generator=torch.Generator().manual_seed(0))
        test_set = val_set
    else:
        try:
            train_set = CarvanaDataset(dir_train_img, dir_train_mask, img_scale)
        except (AssertionError, RuntimeError, IndexError):
            train_set = BasicDataset(dir_train_img, dir_train_mask, img_scale)

        try:
            val_set = CarvanaDataset(dir_val_img, dir_val_mask, img_scale)
        except (AssertionError, RuntimeError, IndexError):
            val_set = BasicDataset(dir_val_img, dir_val_mask, img_scale)
        
        try:
            test_set = CarvanaDataset(dir_test_img, dir_test_mask, img_scale)
        except (AssertionError, RuntimeError, IndexError):
            test_set = BasicDataset(dir_test_img, dir_test_mask, img_scale)

    train_set = train_set
    val_set = val_set
    test_set = test_set

    # 3. Create data loaders
    loader_args = dict(batch_size=batch_size, num_workers=os.cpu_count(), pin_memory=True)
    train_loader = DataLoader(train_set, shuffle=False, **loader_args)
    val_loader = DataLoader(val_set, shuffle=False, drop_last=True, **loader_args)
    test_loader = DataLoader(test_set, shuffle=False, drop_last=True, **loader_args)

    train_score = evaluate(net, train_loader, device, args.amp)
    val_score = evaluate(net, val_loader, device, args.amp)
    test_score = evaluate(net, test_loader, device, args.amp)

    print(f'Training Dice score: {train_score:.4f}')
    print(f'Validation Dice score: {val_score:.4f}')
    print(f'Test Dice score: {test_score:.4f}')

    # Save scores in a CSV file
    scores = [
        ['Dataset', 'Score'],
        ['Training', train_score.item()],
        ['Validation', val_score.item()],
        ['Test', test_score.item()]
    ]

    train_path = Path('./results/train/')
    val_path = Path('./results/val/')
    test_path = Path('./results/test/')
    if not train_path.exists():
        train_path.mkdir(parents=True)
    if not val_path.exists():
        val_path.mkdir(parents=True)
    if not test_path.exists():
        test_path.mkdir(parents=True)

    scores_path = Path('./results/scores.csv')
    # if not scores_path.exists():
    #     scores_path.mkdir(parents=True)

    with open(scores_path, 'w', newline='') as file:
        writer = csv.writer(file)
        writer.writerows(scores)

    print('Scores saved in scores.csv')

    # 4. Qualitative evaluation
    qualitative_results(train_set, train_path, net, device)
    qualitative_results(val_set, val_path, net, device)
    qualitative_results(test_set, test_path, net, device)
    print('Qualitative results saved in ./results/')
