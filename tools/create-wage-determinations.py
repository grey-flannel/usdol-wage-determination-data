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
    document = document.split('================================================================')[0]
    document += '=========='
    groups = re.findall(r'[A-Z]{4}[0-9]{4}-[0-9]{3}.*?(?:----------|==========)', document, re.DOTALL)
    groups = (g.replace('----------', '').replace('==========', '').strip() for g in groups)
    groups = (re.sub(r'\s*Rates\s*Fringes\s*', '\n\n', g) for g in groups)
    return groups


def num_experience_splits_present(document):
    expected_len_job_wages = len(re.findall(r'\.+\$', document))


def welders_present(document):
    return 'WELDERS - Receive rate prescribed for craft' in document


def add_welders_if_present(document, job_wages):
    if welders_present(document):
        rate_identifier = ''
        survey_date = get_publication_date(document)
        job = {'classification': 'WELDER'}
        job_wages.append((rate_identifier, survey_date, job, None))


def validate_job_wages(document, job_wages):
    # TODO beef this up by doing more checks
    expected_len_job_wages = len(re.findall(r'\.+\$', document))
    if welders_present(document):
        expected_len_job_wages += 1
    actual_len_job_wages = len(job_wages)
    if len(job_wages) != expected_len_job_wages:
        raise ValueError(f'Parsed {actual_len_job_wages} job wages but expected {expected_len_job_wages}')


def parse_wage_group(wage_group):
    wage_group_items = wage_group.split('\n\n')
    rate_identifier, survey_date = wage_group_items[0].split()
    survey_date = datetime.datetime.strptime(survey_date, '%m/%d/%Y').date().isoformat()
    classification_groups = wage_group_items[1:]
    return rate_identifier, survey_date, classification_groups


def get_job(classification, subclassification=''):
    classification = re.sub(r'\s+', ' ', classification).strip()
    classification = re.sub(r'\.*$', '', classification)
    subclassification = re.sub(r'\s+', ' ', subclassification).strip()
    subclassification = re.sub(r'\.*$', '', subclassification)
    if subclassification:
        subclassification = subclassification.replace('(', '').replace(')', '')
        classification = f'{classification} ({subclassification.strip()})'
    return {'classification': classification}


def get_wage(wage_group):
    wage_group = re.sub(r'\s+', ' ', wage_group).strip()
    wage_items = wage_group.split()
    rate = wage_items[0]
    if wage_items[1] == '**':
        fringe = wage_items[2]
    else:
        fringe = wage_items[1]
    fringe = re.sub(r'\+[a-z]', '', fringe)
    if '%+' in fringe:
        fringe_percentage, fringe_fixed = fringe.split('%+')
        fringe_percentage = float(fringe_percentage) / 100.0
        fringe = {'fixed': fringe_fixed, 'percentage': f'{fringe_percentage:0.03f}'}
    elif fringe.endswith('%'):
        fringe_percentage = float(fringe[:-1]) / 100.0
        fringe = {'fixed': fringe_fixed, 'percentage': f'{fringe_percentage:0.03f}'}
    if '%' not in fringe:
        fringe = f'0%+{fringe}'
    if '+' not in fringe:
        fringe = f'{fringe}+0.00'
    
    
    return {'rate': rate, 'fringe': fringe}


def process_classification_lines(rate_identifier, survey_date, classification_lines, job_wages):
    classification = ''
    subclassification = ''
    for classification_line in classification_lines:
        is_indented = classification_line.startswith(' ')
        has_wage = '.$ ' in classification_line
        if is_indented:
            subclassification = subclassification + classification_line
        else:
            classification += classification_line
        if not has_wage:
            continue
        if is_indented:
            subclassification, wage_group = subclassification.split('.$')
            job = get_job(classification, subclassification)
        else:
            classification, wage_group = classification.split('.$')
            job = get_job(classification)
            classification = ''
        subclassification = ''
        wage = get_wage(wage_group)
        if job['classification'] == 'ELEVATOR MECHANIC':
            job['classification'] = 'ELEVATOR MECHANIC (Under 5 years)'
            wage['fringe']['percentage'] = '0.06'
            job_wages.append((rate_identifier, survey_date, job, wage))
            job['classification'] = 'ELEVATOR MECHANIC (Over 5 years)'
            wage['fringe']['percentage'] = '0.08'
            job_wages.append((rate_identifier, survey_date, job, wage))
        else:
            job_wages.append((rate_identifier, survey_date, job, wage))
        


def get_job_wages(document):
    wage_groups = get_wage_groups(document)
    job_wages = []
    for wage_group in wage_groups:
        rate_identifier, survey_date, classification_groups = parse_wage_group(wage_group)
        for classifiction_group in classification_groups:
            classification_lines = classifiction_group.splitlines()
            process_classification_lines(rate_identifier, survey_date, classification_lines, job_wages)
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
        if wage is not None:
            wage_determination['wage'] = wage
        else:
            del wage_determination['wage']
        wage_determinations.append(WageDetermination(**wage_determination))
    return wage_determinations


def serialize_wage_determinations(wage_determinations):
    serialized_wage_determinations = [orjson.loads(w.model_dump_json()) for w in wage_determinations]
    deserialized_wage_determinations = [WageDetermination(**w) for w in serialized_wage_determinations]
    return serialized_wage_determinations


def write_wage_determination_document(decision_number, modification_number, state, document):
    document_path = os.path.join('data', 'documents', state)
    document_filename = os.path.join(document_path, f'{decision_number}.{modification_number}.txt')
    os.makedirs(document_path, exist_ok=True)
    print(f'Writing wage determination document to {document_filename}')
    with open(document_filename, 'w') as document_file:
        document_file.write(document)


def write_wage_determination_records(decision_number, modification_number, state, wage_determinations):
    wage_determination_path = os.path.join('data', 'wage-determinations', state)
    wage_determination_filename = os.path.join(wage_determination_path, f'{decision_number}.{modification_number}.json')
    os.makedirs(wage_determination_path, exist_ok=True)
    print(f'Writing wage determination record to {wage_determination_filename}')
    with open(wage_determination_filename, 'wb') as wage_determination_file:
        wage_determination_records = {f'{decision_number}.{modification_number}': wage_determinations}
        wage_determination_file.write(orjson.dumps(wage_determination_records, option=orjson.OPT_INDENT_2))


record_list = [r for n in decision_numbers for r in get_wage_determination_records(n)]

print(f'Collected {len(record_list)} wage determination documents to be parsed')


for decision_number, modification_number, active, state, document in record_list:
    write_wage_determination_document(decision_number, modification_number, state, document)
    try:
        wage_determinations = create_wage_determinations(decision_number, modification_number, active, state, document)
    except Exception as error:
        print(f'Unable to parse document {decision_number}.{modification_number}: {error}')
        continue
    try:
        wage_determinations = serialize_wage_determinations(wage_determinations)
    except Exception as error:
        print(f'Unable to validate wage determinations from {decision_number}.{modification_number}: {error}')
        continue
    write_wage_determination_records(decision_number, modification_number, state, wage_determinations)
