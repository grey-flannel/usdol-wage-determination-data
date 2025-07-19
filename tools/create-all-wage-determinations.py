import datetime
import os
import re
import sys

import cachier
import httpx
import orjson

from usdol_wage_determination_model import WageDetermination


try:
    decision_numbers = [d.upper() for d in sys.argv[1].split(',')]
except IndexError:
    decision_numbers = None


http_client = httpx.Client()


@cachier.cachier()
def get_wage_determination_record(decision_number, modification_number):
    api_url = f'https://sam.gov/api/prod/wdol/v1/wd/{decision_number}/{modification_number}'
    response = http_client.get(api_url)
    try:
        return response.json()
    except Exception:
        return None


def get_record_identifier(record):
    decision_number = record['fullReferenceNumber'].upper()
    modification_number = record['revisionNumber']
    return f'{decision_number}.{modification_number}'


with open('data/index.json') as index_file:
    index = orjson.loads(index_file.read())
    record_map = {get_record_identifier(r): r for r in index['records']}


def make_record(record):
    decision_number = record['fullReferenceNumber'].upper()
    modification_number = record['revisionNumber']
    state = record['location']['state']['code']
    return decision_number, modification_number, state, record


def get_record_list():
    if decision_numbers is not None:
        return [make_record(record_map[d]) for d in decision_numbers]
    else:
        return list(record_map.values())


def get_superseding_record(record):
    decision_number = record['fullReferenceNumber'].upper()
    modification_number = record['revisionNumber']
    next_identifier = f'{decision_number}.{modification_number + 1}'
    if next_identifier in record_map:
        return record_map[next_identifier]
    next_year = int(decision_number[2:6]) + 1
    next_identifier = f'{decision_number[0:2]}{next_year}{decision_number[6:]}.0'
    if next_identifier in record_map:
        return record_map[next_identifier]
    return None


def get_publication_date(record):
    # First try to get value from the JSON record
    if 'publishDate' in record:
        publish_timestamp = record['publishDate'] / 1000
        return datetime.datetime.fromtimestamp(publish_timestamp, datetime.UTC).date().isoformat()
    # Fall back to parsing from the document if that doesn't work
    match = re.search(r'General Decision Number: ([A-Z]{2}[0-9]{8}) ([0-9/]{10})', document)
    return datetime.datetime.strptime(match[2], '%m/%d/%Y').date().isoformat()


def get_effective_dates(record):
    effective_dates = {'start_date': record['publication_date']}
    superseding_record = get_superseding_record(record)
    if superseding_record is not None:
        effective_dates['end_date'] = superseding_record['publication_date']
    return effective_dates


def get_counties(record, document):
    # First try to get values from the JSON record
    counties = record['location']['state']['counties']
    if counties is not None:
        return [c['value'] for c in counties]
    # Fall back to parsing from the document if that doesn't work
    match = re.search(r'County: (\S+) County in', document)
    return [match[1]]


def get_construction_types(record, document):
    # First try to get values from the JSON record
    if 'constructionTypes' in record:
        return [c.lower() for c in record['constructionTypes']]
    # Fall back to parsing from the document if that doesn't work
    match = re.search(r'Construction Types?: (\w+)', document)
    return [match[1].lower()]


def get_wage_groups(document):
    document = document.replace('\n', ' ')
    document = document.split('================================================================')[0]
    groups = re.findall(r'[A-Z]{4}[0-9]{4}-[0-9]{3}.*?----- ', document)
    groups = (g.replace('----------------------------------------------------------------', '') for g in groups)
    groups = (re.sub(r'\s+', ' ', g) for g in groups)
    groups = (g.replace(' Rates Fringes ', ' ') for g in groups)
    groups = (g.replace(' ** ', ' ') for g in groups)
    groups = (re.sub(r'\.\.+\$ ', ' ', g) for g in groups)
    return groups


def get_job_wages(document):
    wage_groups = get_wage_groups(document)
    job_wages = []
    for wage_group in wage_groups:
        rate_identifier = wage_group[0:12]
        survey_date = datetime.datetime.strptime(wage_group[13:23], '%m/%d/%Y').date().isoformat()
        groups = re.findall(r'.*? \d+\.\d{2} \d+\.\d{2}', wage_group[24:])
        classification_prefix = None
        for group in groups:
            chunks = group.strip().split(' ')
            classification = ' '.join(chunks[:-2])
            match = re.search(r'(\(\d+\))', classification)
            if match:
                numbered_bullet = match[1]
                start, end = match.span()
                if numbered_bullet == '(1)':
                    classification_prefix = classification[:start].strip()
                classification = classification_prefix + classification[end:]
            rate = chunks[-2]
            fringe = chunks[-1]
            job = {'classification': classification}
            wage = {'rate': rate, 'fringe': fringe}
            job_wages.append((rate_identifier, survey_date, job, wage))
    return job_wages


def create_wage_determinations(record, document):
    publication_date = get_publication_date(record)
    effective_dates = get_effective_dates()
    construction_types = get_construction_types(record, document)
    state = record['location']['state']['code'].upper()
    counties = get_counties(record, document)
    job_wages = get_job_wages(document)
    wage_determination = {
        'decision_number': record['fullReferenceNumber'].upper(),
        'modification_number': record['revisionNumber'],
        'publication_date': publication_date,
        'effective': effective_dates,
        'active': record['isActive'],
    }
    items = ((t, c, r, s, j, w) for t in construction_types for c in counties for r, s, j, w in job_wages)
    wage_determinations = []
    for construction_type, county, rate_identifier, survey_date, job, wage in items:
        wage_determination['construction_type'] = construction_type
        wage_determination['location'] = {'state': state, 'county': county}
        wage_determination['rate_identifier'] = rate_identifier
        wage_determination['survey_date'] = survey_date
        wage_determination['job'] = job
        wage_determination['wage'] = wage
        wage_determinations.append(WageDetermination(**wage_determination))
    return wage_determinations


record_list = get_record_list()


print(f'Creating {len(record_list)} records')


for decision_number, modification_number, state, record in record_list:
    document_path = os.path.join('data', 'documents', state)
    document_filename = os.path.join(document_path, f'{decision_number}.{modification_number}.txt')
    wage_determination_path = os.path.join('data', 'wage-determinations', state)
    wage_determination_filename = os.path.join(wage_determination_path, f'{decision_number}.{modification_number}.json')
    with open(document_filename, 'rb') as document_file:
        document = document_file.read().decode('latin-1')
    os.makedirs(wage_determination_path, exist_ok=True)
    wage_determinations = create_wage_determinations(record, document)
    serialized_wage_determinations = {decision_number: [orjson.loads(w.model_dump_json()) for w in wage_determinations]}
    deserialized_wage_determinations = [WageDetermination(**w) for w in serialized_wage_determinations[decision_number]]
    print(f'Writing wage determination record to {wage_determination_filename}')
    with open(wage_determination_filename, 'wb') as wage_determination_file:
        wage_determination_file.write(orjson.dumps(serialized_wage_determinations, option=orjson.OPT_INDENT_2))
