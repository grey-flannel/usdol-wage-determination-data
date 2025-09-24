import datetime
import os
import itertools
import re
import sys

import cachier
import httpx
import orjson

from usdol_wage_determination_model import WageDetermination


decision_numbers_parameter = sys.argv[1]
try:
    with open(decision_numbers_parameter) as decsion_numbers_file:
        decision_numbers = [d.strip() for d in decsion_numbers_file if d.strip()]
except Exception:
    decision_numbers = [d.upper() for d in decision_numbers_parameter.split(',')]


http_client = httpx.Client()


def split_text_list(text):
    text = text.replace(' and ', ', ').replace(',,', ',')
    return text.split(', ')


@cachier.cachier()
def get_wage_determination_record(decision_number, modification_number):
    print(f'Fetching wage determination record {decision_number} modification {modification_number}')
    api_url = f'https://sam.gov/api/prod/wdol/v1/wd/{decision_number}/{modification_number}'
    response = http_client.get(api_url)
    try:
        record = response.json()
        decision_number = record['fullReferenceNumber'].upper()
        modification_number = record['revisionNumber']
        active = record.get('active', False)
        state = decision_number[0:2]
        document = record['document']
        return (decision_number, modification_number, active, state, document)
    except Exception:
        return {}


def get_wage_determination_records(decision_number):
    modification_numbers = itertools.count()
    records = (get_wage_determination_record(decision_number, m) for m in modification_numbers)
    return itertools.takewhile(lambda r: r, records)


def get_publication_date(document):
    match = re.search(r'General Decision Number: ([A-Z]{2}[0-9]{6,8}) ([0-9/]{10})', document)
    return datetime.datetime.strptime(match[2], '%m/%d/%Y').date().isoformat()


def get_superseding_record(decision_number, modification_number):
    superseding_record = get_wage_determination_record(decision_number, modification_number + 1)
    if superseding_record:
        return superseding_record
    next_year = int(decision_number[2:6]) + 1
    next_decision_number = f'{decision_number[0:2]}{next_year}{decision_number[6:]}'
    superseding_record = get_wage_determination_record(next_decision_number, 0)
    return superseding_record


def get_effective_dates(decision_number, modification_number, publication_date):
    effective_dates = {'start_date': publication_date}
    superseding_record = get_superseding_record(decision_number, modification_number)
    if superseding_record:
        effective_dates['end_date'] = get_publication_date(superseding_record[4])
    return effective_dates


def get_counties(document):
    document = document.replace('\n', ' ')
    match = re.search(r'Count(?:y|ies): (.+) Count(?:y|ies) in', document)
    if match:
        return split_text_list(match[1])
    else:
        return None


def get_construction_types(document):
    match = re.search(r'Construction Types?: (\w+)', document)
    if match:
        return split_text_list(match[1].lower())
    else:
        return None


def get_wage_groups(document):
    document = document.replace('\n', ' ')
    document = document.split('================================================================')[0]
    groups = re.findall(r'[A-Z]{4}[0-9]{4}-[0-9]{3}.*?----- ', document)
    groups = (g.replace('----------------------------------------------------------------', '') for g in groups)
    groups = (re.sub(r'\s+', ' ', g) for g in groups)
    # groups = (re.sub(r'\s+', ' ', g) for g in groups)
    groups = (g.replace(' Rates Fringes ', ' ') for g in groups)
    groups = (g.replace(' ** ', ' ') for g in groups)
    groups = (re.sub(r'\.+\$ ', ' $ ', g) for g in groups)
    return groups


def welders_present(document):
    return 'WELDERS - Receive rate prescribed for craft' in document


def add_welders_if_present(document, job_wages):
    if welders_present(document):
        rate_identifier = ''
        survey_date = get_publication_date(document)
        job = {'classification': 'WELDER'}
        wage = {'rate': 0.0, 'fringe': 0.0}
        job_wages.append((rate_identifier, survey_date, job, wage))


def validate_job_wages(document, job_wages):
    # TODO beef this up by doing more checks
    expected_len_job_wages = len(re.findall(r'\.+\$', document))
    if welders_present(document):
        expected_len_job_wages += 1
    actual_len_job_wages = len(job_wages)
    if len(job_wages) != expected_len_job_wages:
        for jw in job_wages:
            print(jw)
        raise ValueError(f'Parsed {actual_len_job_wages} job wages but expected {expected_len_job_wages}')


def parse_wage_group(wage_group):
    rate_identifier = wage_group[0:12]
    survey_date = datetime.datetime.strptime(wage_group[13:23], '%m/%d/%Y').date().isoformat()
    classification_groups = re.findall(r'.*? \$ \S+ \S+', wage_group[24:])
    return rate_identifier, survey_date, classification_groups


def get_classification_prefix(classification):
    words = classification.split(' ')
    uppercase_words = (w for w in words if not re.search(r'[a-z]', w))
    return ' '.join(uppercase_words)


def get_job(classification, classification_prefix):
    print(classification)
    if classification.startswith(' '):
        classification = classification_prefix + classification
    else:
        classification_prefix = get_classification_prefix(classification)
    # TO-DO fix up classification to consistently format descriptor after classification
    # TO-DO consider a sub-classification in the model?
    return {'classification': classification}, classification_prefix


def get_wage(rate, fringe):
    fringe = re.sub(r'\+[a-z]', '', fringe)
    if '%' not in fringe:
        fringe = f'0%+{fringe}'
    if '+' not in fringe:
        fringe = f'{fringe}+0.00'
    fringe_percentage, fringe_fixed = fringe.split('%+')
    fringe_value = float(rate) * float(fringe_percentage) / 100.0 + float(fringe_fixed)
    fringe = f'{fringe_value:0.02f}'
    return {'rate': rate, 'fringe': fringe}


def get_job_wages(document):
    wage_groups = get_wage_groups(document)
    job_wages = []
    for wage_group in wage_groups:
        print(wage_group)
        continue
        rate_identifier, survey_date, classification_groups = parse_wage_group(wage_group)
        classification_prefix = None
        for classifiction_group in classification_groups:
            classification, wage_group = classifiction_group.split(' $ ')
            rate, fringe = wage_group.split()
            job, classification_prefix = get_job(classification, classification_prefix)
            wage = get_wage(rate, fringe)
            job_wages.append((rate_identifier, survey_date, job, wage))
    add_welders_if_present(document, job_wages)
    validate_job_wages(document, job_wages)
    return job_wages


def create_wage_determinations(decision_number, modification_number, active, state, document):
    publication_date = get_publication_date(document)
    effective_dates = get_effective_dates(decision_number, modification_number, publication_date)
    construction_types = get_construction_types(document)
    counties = get_counties(document)
    job_wages = get_job_wages(document)
    wage_determination = {
        'decision_number': decision_number,
        'modification_number': modification_number,
        'publication_date': publication_date,
        'effective': effective_dates,
        'active': active,
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


record_list = [r for n in decision_numbers for r in get_wage_determination_records(n)]

print(f'Collected {len(record_list)} wage determination documents to be parsed')


for decision_number, modification_number, active, state, document in record_list:
    wage_determination_path = os.path.join('data', 'wage-determinations', state)
    wage_determination_filename = os.path.join(wage_determination_path, f'{decision_number}.{modification_number}.json')
    os.makedirs(wage_determination_path, exist_ok=True)
    try:
        wage_determinations = create_wage_determinations(decision_number, modification_number, active, state, document)
    except Exception as error:
        print(f'Unable to parse document {decision_number}.{modification_number}: {error}')
        print(document)
        raise
    serialized_wage_determinations = [orjson.loads(w.model_dump_json()) for w in wage_determinations]
    deserialized_wage_determinations = [WageDetermination(**w) for w in serialized_wage_determinations]
    print(f'Writing wage determination record to {wage_determination_filename}')
    with open(wage_determination_filename, 'wb') as wage_determination_file:
        wage_determination_records = {f'{decision_number}.{modification_number}': serialized_wage_determinations}
        wage_determination_file.write(orjson.dumps(wage_determination_records, option=orjson.OPT_INDENT_2))
