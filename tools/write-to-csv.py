import csv
import sys

import orjson

from usdol_wage_determination_model import WageDetermination


input_filename = sys.argv[1]
output_filename = sys.argv[2]


field_names = (
    'decision_number',
    'modification_number',
    'publication_date',
    'effective.start_date',
    'effective.end_date',
    'active',
    'construction_type',
    'location.state',
    'location.county',
    'location.zone.center.latitude',
    'location.zone.center.longitude',
    'location.zone.radius_min',
    'location.zone.radius_max',
    'survey_date',
    'job.classification',
    'wage.currency',
    'wage.rate',
    'wage.fringe',
)


with open(input_filename) as input_file:
    records = orjson.loads(input_file.read())


wage_determinations = [WageDetermination(**r) for i in records for r in records[i]]
wage_determination_rows = [w.model_dump_tuple() for w in wage_determinations]


with open(output_filename, 'w') as output_file:
    writer = csv.writer(output_file)
    writer.writerow(field_names)
    writer.writerows(wage_determination_rows)


print(f'Wrote {len(wage_determination_rows)} total records to {output_filename}')
