import json
import os

NLVR2_DATA_ROOT = '../nlvr/nlvr2/data'

split2fname = {
    'train': 'train',
    'valid': 'dev',
    'test': 'test1',
    #'hidden': 'test2'
}

for split, fname in split2fname.items():
    with open(os.path.join(NLVR2_DATA_ROOT, f'{fname}.json')) as f:
        new_data = []
        for i, line in enumerate(f):
            datum = json.loads(line)
            id_stem = '-'.join(datum['identifier'].split('-')[:-1])
            new_datum = {
                'identifier': datum['identifier'],
                'img0': f'{id_stem}-img0',
                'img1': f'{id_stem}-img1',
                'label': 1 if datum['label'] == 'True' else 0,
                'sent': datum['sentence'],
                'uid': 'nlvr2_%s_%d' % (split, i),
            }
            new_data.append(new_datum)

    with open(f'../{split}.json', 'w') as g:
        json.dump(new_data, g, sort_keys=True, indent=4)
