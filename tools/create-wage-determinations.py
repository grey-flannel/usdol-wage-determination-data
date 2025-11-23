import copy
import datetime
import decimal
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
def get_wage_determination_record_from_sam(decision_number, modification_number):
    state = decision_number[0:2]
    api_url = f'https://sam.gov/api/prod/wdol/v1/wd/{decision_number}/{modification_number}'
    print(f'Fetching {decision_number} modification {modification_number} from {api_url}')
    response = http_client.get(api_url)
    try:
        record = response.json()
        active = record.get('active', False)
        document = record['document']
        return (decision_number, modification_number, active, state, document)
    except Exception:
        return None


def get_wage_determination_record_from_document(decision_number, modification_number):
    state = decision_number[0:2]
    document_filename = f'data/documents/{state}/{decision_number}.{modification_number}.txt'
    print(f'Fetching {decision_number} modification {modification_number} from {document_filename}')
    try:
        with open(document_filename) as document_file:
            active = False  # This is an assumption but mostly likely all local doc records are inactive
            document = document_file.read()
        return (decision_number, modification_number, active, state, document)
    except Exception:
        return None


def get_wage_determination_record(decision_number, modification_number):
    sam_record = get_wage_determination_record_from_sam(decision_number, modification_number)
    if sam_record:
        return sam_record
    file_record = get_wage_determination_record_from_document(decision_number, modification_number)
    if file_record:
        return file_record
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
    document = re.sub(r'\s+', ' ', document).strip()
    match = re.search(r'Count(?:y|ies): (.+) Count(?:y|ies) in', document)
    if match:
        return split_text_list(match[1])
    else:
        return []


def get_construction_types(document):
    document = re.sub(r'\s+', ' ', document).strip()
    match = re.search(r'Construction Types?: (\w+)', document)
    if match:
        return split_text_list(match[1].lower())
    else:
        return []


def get_wage_groups(document):
    document = document.split('================================================================')[0]
    document += '=========='
    wage_group_pattern = r'(?:[A-Z]{4}[0-9]{4}-[0-9]{3}|[A-Z]{4}-[A-Z]{2}-[0-9]{4}).*?(?:----------|==========)'
    groups = re.findall(wage_group_pattern, document, re.DOTALL)
    groups = (g.replace('----------', '').replace('==========', '').strip() for g in groups)
    groups = (re.sub(r'\s*Rates\s*Fringes\s*', '\n\n', g) for g in groups)
    return groups


def num_rate_rows(document):
    return len(re.findall(r'\.+\$', document))


def num_experience_splits(state, document):
    if state == 'AZ' and 'AZ20220031' in document:
        return 7
    elif state == 'CO' and 'CO20230022' in document:
        return 1
    elif state == 'GA' and 'GA20220050' in document:
        return 2
    elif state == 'MO' and 'MO20220050' in document:
        return 1
    elif state == 'NH' and 'NH20200021' in document:
        return 1
    elif state == 'NM' and 'NM20240044' in document:
        return 1
    elif state == 'OH' and 'OH20220086' in document:
        return 1
    elif state == 'PA' and 'PA20240101' in document:
        return 1
    elif state == 'TX' and 'TX20240271' in document:
        return 1
    elif state == 'TX' and 'TX20240275' in document:
        return 1
    elif state == 'UT' and 'UT20240087' in document:
        return 1
    else:
        return 0


def welders_present(document):
    return 'WELDERS - Receive rate prescribed for craft' in document


def validate_job_wages(state, document, job_wages):
    expected_len_job_wages = num_rate_rows(document)
    expected_len_job_wages += num_experience_splits(state, document)
    if welders_present(document):
        expected_len_job_wages += 1
    actual_len_job_wages = len(job_wages)
    if len(job_wages) != expected_len_job_wages:
        raise ValueError(f'Parsed {actual_len_job_wages} job wages but expected {expected_len_job_wages}')


def add_welders_if_present(document, job_wages):
    if welders_present(document):
        rate_identifier = ''
        survey_date = get_publication_date(document)
        job = {'classification': 'WELDER'}
        job_wages.append((rate_identifier, survey_date, job, None))


def parse_wage_group(wage_group):
    wage_group_items = wage_group.split('\n\n')
    rate_identifier, survey_date = wage_group_items[0].split()
    survey_date = datetime.datetime.strptime(survey_date, '%m/%d/%Y').date().isoformat()
    classification_groups = wage_group_items[1:]
    return rate_identifier, survey_date, classification_groups


def get_job(classification, subclassification=''):
    classification = re.sub(r'\s+', ' ', classification).strip()
    classification = re.sub(r'\(\d+\)', '', classification)
    classification = re.sub(r'\.*$', '', classification)
    subclassification = re.sub(r'\s+', ' ', subclassification).strip()
    subclassification = re.sub(r'\(\d+\)', '', subclassification)
    subclassification = re.sub(r'\.*$', '', subclassification)
    if subclassification:
        subclassification = subclassification.replace('(', '').replace(')', '')
        classification = f'{classification} ({subclassification.strip()})'
    return {'classification': classification}


def apply_special_case_modifications(state, rate_identifier, survey_date, job, wage, footnotes):
    job_wages = [(rate_identifier, survey_date, job, wage)]
    if state == 'AZ' and rate_identifier == 'BRAZ0003-009':
        location = 'miles from the intersection of Central Ave, and Washington St., Phoenix, AZ'
        modifications = [
            (f' (Zone A: 0-60 {location})', decimal.Decimal('0.00')),
            (f' (Zone B: 61-75 {location})', decimal.Decimal('2.00')),
            (f' (Zone C: 75-100 {location})', decimal.Decimal('3.00')),
            (f' (Zone D: 101-200 {location})', decimal.Decimal('3.50')),
            (f' (Zone E: Over 200 {location})', decimal.Decimal('6.50')),
        ]
        job_wages = []
        for classification_suffix, rate_increase in modifications:
            modified_job = copy.deepcopy(job)
            modified_wage = copy.deepcopy(wage)
            modified_job['classification'] += classification_suffix
            modified_wage['rate'] = str(decimal.Decimal(modified_wage['rate']) + rate_increase)
            job_wages.append((rate_identifier, survey_date, modified_job, modified_wage))
    if state == 'AZ' and rate_identifier == 'IRON0075-011':
        location = 'miles from City Hall in Phoenix or Tucson'
        modifications = [
            (f' (Zone 1: 0-60 {location})', decimal.Decimal('0.00')),
            (f' (Zone 2: 61-75 {location})', decimal.Decimal('4.00')),
            (f' (Zone 3: 75-100 {location})', decimal.Decimal('5.00')),
            (f' (Zone 4: 101-200 {location})', decimal.Decimal('6.50')),
        ]
        job_wages = []
        for classification_suffix, rate_increase in modifications:
            modified_job = copy.deepcopy(job)
            modified_wage = copy.deepcopy(wage)
            modified_job['classification'] += classification_suffix
            modified_wage['rate'] = str(decimal.Decimal(modified_wage['rate']) + rate_increase)
            job_wages.append((rate_identifier, survey_date, modified_job, modified_wage))
    if state == 'AZ' and rate_identifier == 'PAIN0086-006':
        job_wages = []
        if 'ZONE A' in job['classification']:
            zone_a_note = 'ZONE A: Free Zone: A distance of 0 to 100 miles from the old Phoenix courthouse'
            job['classification'] = job['classification'].replace('ZONE A', zone_a_note)
        if 'ZONE B' in job['classification']:
            zone_b_note = 'ZONE B: A distance of 101 miles and over from the old Phoenix courthouse'
            job['classification'] = job['classification'].replace('ZONE B', zone_b_note)
        job_wages.append((rate_identifier, survey_date, job, wage))
    if state == 'CO' and job['classification'].startswith('ELEVATOR MECHANIC'):
        modifications = [
            (' (Under 5 years)', '0.06'),
            (' (Over 5 years)', '0.08'),
        ]
        job_wages = []
        for classification_suffix, fringe_percentage in modifications:
            modified_job = copy.deepcopy(job)
            modified_wage = copy.deepcopy(wage)
            modified_job['classification'] += classification_suffix
            modified_wage['fringe']['percentage'] = fringe_percentage
            modified_wage['fringe']['holidays'] = {
                'New Year\'s Day',
                'Memorial Day',
                'Independence Day',
                'Labor Day',
                'Veterans Day',
                'Thanksgiving Day',
                'Day After Thanksgiving',
                'Christmas Day',
            }
            job_wages.append((rate_identifier, survey_date, modified_job, modified_wage))
    if state == 'GA' and rate_identifier == 'SHEE0085-004':
        message = 'Work on swinging stages, boatswains chairs or scaffolds, booms, or scissors lifts over 50 ft. high'
        modifications = [
            ('', decimal.Decimal('0.00')),
            (f' ({message})', decimal.Decimal('1.25')),
        ]
        job_wages = []
        for classification_suffix, rate_increase in modifications:
            modified_job = copy.deepcopy(job)
            modified_wage = copy.deepcopy(wage)
            modified_job['classification'] += classification_suffix
            modified_wage['rate'] = str(decimal.Decimal(modified_wage['rate']) + rate_increase)
            job_wages.append((rate_identifier, survey_date, modified_job, modified_wage))
    if state == 'MO' and job['classification'].startswith('ELEVATOR MECHANIC'):
        modifications = [
            (' (6 months to 5 years)', '0.06'),
            (' (5 years or more)', '0.08'),
        ]
        job_wages = []
        for classification_suffix, fringe_percentage in modifications:
            modified_job = copy.deepcopy(job)
            modified_wage = copy.deepcopy(wage)
            modified_job['classification'] += classification_suffix
            modified_wage['fringe']['percentage'] = fringe_percentage
            modified_wage['fringe']['holidays'] = {
                'New Year\'s Day',
                'Memorial Day',
                'Independence Day',
                'Labor Day',
                'Veterans Day',
                'Thanksgiving Day',
                'Day After Thanksgiving',
                'Christmas Day',
            }
            job_wages.append((rate_identifier, survey_date, modified_job, modified_wage))
    if state == 'MO' and job['classification'].startswith('TRUCK DRIVER'):
        job_wages = []
        modified_wage = copy.deepcopy(wage)
        modified_wage['fringe']['holidays'] = {
            'New Year\'s Day',
            'Memorial Day',
            'Independence Day',
            'Labor Day',
            'Thanksgiving Day',
            'Christmas Day',
        }
        job_wages.append((rate_identifier, survey_date, job, modified_wage))
    if state == 'NH' and job['classification'].startswith('ELEVATOR MECHANIC'):
        modifications = [
            (' (6 months to 5 years)', '0.06'),
            (' (5 years or more)', '0.08'),
        ]
        job_wages = []
        for classification_suffix, fringe_percentage in modifications:
            modified_job = copy.deepcopy(job)
            modified_wage = copy.deepcopy(wage)
            modified_job['classification'] += classification_suffix
            modified_wage['fringe']['percentage'] = fringe_percentage
            modified_wage['fringe']['holidays'] = {
                'New Year\'s Day',
                'Memorial Day',
                'Independence Day',
                'Labor Day',
                'Veterans Day',
                'Thanksgiving Day',
                'Day After Thanksgiving',
                'Christmas Day',
            }
            job_wages.append((rate_identifier, survey_date, modified_job, modified_wage))
    if state == 'NM' and job['classification'].startswith('ELEVATOR MECHANIC'):
        modifications = [
            (' (Less than 5 years)', '0.06'),
            (' (More than 5 years)', '0.08'),
        ]
        job_wages = []
        for classification_suffix, fringe_percentage in modifications:
            modified_job = copy.deepcopy(job)
            modified_wage = copy.deepcopy(wage)
            modified_job['classification'] += classification_suffix
            modified_wage['fringe']['percentage'] = fringe_percentage
            modified_wage['fringe']['holidays'] = {
                'New Year\'s Day',
                'Memorial Day',
                'Independence Day',
                'Labor Day',
                'Veterans Day',
                'Thanksgiving Day',
                'Day After Thanksgiving',
                'Christmas Day',
            }
            job_wages.append((rate_identifier, survey_date, modified_job, modified_wage))
    if state == 'OH' and job['classification'].startswith('ELEVATOR MECHANIC'):
        modifications = [
            (' (Less than 5 years)', '0.06'),
            (' (More than 5 years)', '0.08'),
        ]
        job_wages = []
        for classification_suffix, fringe_percentage in modifications:
            modified_job = copy.deepcopy(job)
            modified_wage = copy.deepcopy(wage)
            modified_job['classification'] += classification_suffix
            modified_wage['fringe']['percentage'] = fringe_percentage
            modified_wage['fringe']['holidays'] = {
                'New Year\'s Day',
                'Memorial Day',
                'Independence Day',
                'Labor Day',
                'Veterans Day',
                'Thanksgiving Day',
                'Day After Thanksgiving',
                'Christmas Day',
            }
            job_wages.append((rate_identifier, survey_date, modified_job, modified_wage))
    if state == 'PA' and job['classification'].startswith('ELEVATOR MECHANIC'):
        modifications = [
            (' (6 months to 5 years)', '0.06'),
            (' (5 years or more)', '0.08'),
        ]
        job_wages = []
        for classification_suffix, fringe_percentage in modifications:
            modified_job = copy.deepcopy(job)
            modified_wage = copy.deepcopy(wage)
            modified_job['classification'] += classification_suffix
            modified_wage['fringe']['percentage'] = fringe_percentage
            modified_wage['fringe']['holidays'] = {
                'New Year\'s Day',
                'Memorial Day',
                'Independence Day',
                'Labor Day',
                'Veterans Day',
                'Thanksgiving Day',
                'Day After Thanksgiving',
                'Christmas Day',
            }
            job_wages.append((rate_identifier, survey_date, modified_job, modified_wage))
    if state == 'TX' and job['classification'].startswith('ELEVATOR MECHANIC'):
        modifications = [
            (' (Under 5 years)', '0.06'),
            (' (Over 5 years)', '0.08'),
        ]
        job_wages = []
        for classification_suffix, fringe_percentage in modifications:
            modified_job = copy.deepcopy(job)
            modified_wage = copy.deepcopy(wage)
            modified_job['classification'] += classification_suffix
            modified_wage['fringe']['percentage'] = fringe_percentage
            modified_wage['fringe']['holidays'] = {
                'New Year\'s Day',
                'Memorial Day',
                'Independence Day',
                'Labor Day',
                'Veterans Day',
                'Thanksgiving Day',
                'Day After Thanksgiving',
                'Christmas Day',
            }
            job_wages.append((rate_identifier, survey_date, modified_job, modified_wage))
    if state == 'UT' and job['classification'].startswith('ELEVATOR MECHANIC'):
        modifications = [
            (' (Under 5 years)', '0.06'),
            (' (5 or more years)', '0.08'),
        ]
        job_wages = []
        for classification_suffix, fringe_percentage in modifications:
            modified_job = copy.deepcopy(job)
            modified_wage = copy.deepcopy(wage)
            modified_job['classification'] += classification_suffix
            modified_wage['fringe']['percentage'] = fringe_percentage
            modified_wage['fringe']['holidays'] = {
                'New Year\'s Day',
                'Memorial Day',
                'Independence Day',
                'Labor Day',
                'Veterans Day',
                'Thanksgiving Day',
                'Day After Thanksgiving',
                'Christmas Day',
            }
            job_wages.append((rate_identifier, survey_date, modified_job, modified_wage))
    return job_wages


def get_new_job_wages(state, rate_identifier, survey_date, job, wage_group):
    wage_group = re.sub(r'\s+', ' ', wage_group).strip()
    wage_items = wage_group.split()
    num_wage_items = len(wage_items)
    if num_wage_items == 0:
        rate = '0.00'
        fringe_string = '0.00'
    elif num_wage_items == 1:
        rate = wage_items[0]
        fringe_string = '0.00'
    elif num_wage_items == 2:
        rate = wage_items[0]
        fringe_string = '0.00' if wage_items[1] == '**' else wage_items[1]
    elif num_wage_items == 3:
        rate = wage_items[0]
        fringe_string = wage_items[2]
    else:
        raise ValueError
    if fringe_string.startswith('a'):
        fringe_string = f'+{fringe_string}'
    footnotes = re.findall(r'\+[a-z]', fringe_string)
    fringe_string = re.sub(r'\+[a-z]', '', fringe_string)
    if '%+' in fringe_string:
        percentage, fixed = fringe_string.split('%+')
        percentage = float(percentage) / 100.0
        fringe = {'fixed': fixed, 'percentage': f'{percentage:0.03f}'}
    elif fringe_string.endswith('%'):
        percentage = float(fringe_string[:-1]) / 100.0
        fringe = {'percentage': f'{percentage:0.03f}'}
    else:
        fixed = fringe_string if fringe_string else '0.0'
        fringe = {'fixed': fixed}
    wage = {'rate': rate, 'fringe': fringe}
    return apply_special_case_modifications(state, rate_identifier, survey_date, job, wage, footnotes)


def process_classification_lines(state, rate_identifier, survey_date, classification_lines, job_wages):
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
        new_job_wages = get_new_job_wages(state, rate_identifier, survey_date, job, wage_group)
        job_wages.extend(new_job_wages)


def get_job_wages(state, document):
    wage_groups = get_wage_groups(document)
    job_wages = []
    for wage_group in wage_groups:
        rate_identifier, survey_date, classification_groups = parse_wage_group(wage_group)
        for classifiction_group in classification_groups:
            classification_lines = classifiction_group.splitlines()
            process_classification_lines(state, rate_identifier, survey_date, classification_lines, job_wages)
    add_welders_if_present(document, job_wages)
    validate_job_wages(state, document, job_wages)
    return job_wages


def create_wage_determinations(decision_number, modification_number, active, state, document):
    publication_date = get_publication_date(document)
    effective_dates = get_effective_dates(decision_number, modification_number, publication_date)
    construction_types = get_construction_types(document)
    counties = get_counties(document)
    job_wages = get_job_wages(state, document)
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
