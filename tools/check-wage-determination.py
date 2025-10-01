import os
import sys

import orjson


decision_numbers_parameter = sys.argv[1]
try:
    with open(decision_numbers_parameter) as decsion_numbers_file:
        decision_numbers = [d.strip() for d in decsion_numbers_file if d.strip()]
except Exception:
    decision_numbers = [d.upper() for d in decision_numbers_parameter.split(',')]


for decision_number in decision_numbers:
    state = decision_number[:2]
    wage_determination_path = os.path.join('data', 'wage-determinations', state)
    for modification_number in range(9999):
        wage_determination_filename = os.path.join(wage_determination_path, f'{decision_number}.{modification_number}.json')
        try:
            with open(wage_determination_filename) as wage_determination_file:
                print(wage_determination_filename)
                wage_determination_records = orjson.loads(wage_determination_file.read())
                for dm, records in wage_determination_records.items():
                    for w in records:
                        print(w['rate_identifier'], w['survey_date'], w.get('wage'), w['job'])
            os.system(f'subl data/documents/{state}/{decision_number}.{modification_number}.txt')
        except FileNotFoundError:
            break
