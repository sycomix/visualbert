from pathlib import Path
import json

GQA_ROOT = '../'

path = Path(f'{GQA_ROOT}data')
split2name = {
    'train': 'train',
    'valid': 'val',
    'testdev': 'testdev',
    }

for split, name in split2name.items():
    new_data = []
    if split == 'train':
        paths = list((path / 'train_all_questions').iterdir())
    else:
        paths = [path / f"{name}_all_questions.json"]
    print(split, paths)

    for tmp_path in paths:
        with tmp_path.open() as f:
            data = json.load(f)
            for key, datum in data.items():
                new_datum = {
                    'question_id': key,
                    'img_id': datum['imageId'],
                    'sent': datum['question'],
                }
                if 'answer' in datum:
                    new_datum['label'] = {datum['answer']: 1.}
                new_data.append(new_datum)
    print(split, len(new_data))
    json.dump(
        new_data, open(f"../{split}_all.json", 'w'), indent=4, sort_keys=True
    )

