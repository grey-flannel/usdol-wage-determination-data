import sys

import orjson

from usdol_wage_determination_model import WageDetermination


record_filename = sys.argv[1]

with open(record_filename) as record_file:
    record = orjson.loads(record_file.read())


try:
    wage_determination = WageDetermination(**record)
    print('Validated')
    print(wage_determination)
except Exception as error:
    print(f'Failed to validate: {error}')
