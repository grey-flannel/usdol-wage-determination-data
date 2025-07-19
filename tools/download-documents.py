import os
import sys
import time

import httpx
import orjson


try:
    decision_numbers = [d.upper() for d in sys.argv[1].split(',')]
except IndexError:
    decision_numbers = None


http_client = httpx.Client(follow_redirects=True)


def make_record(record):
    decision_number = record['fullReferenceNumber'].upper()
    modification_number = record['revisionNumber']
    state = record['location']['state']['code']
    return decision_number, modification_number, state, record


def get_record_list():
    with open('data/index.json') as index_file:
        index = orjson.loads(index_file.read())
        records = index['records']
    record_list = [make_record(r) for r in records]
    if decision_numbers is not None:
        record_list = [r for r in record_list if r[0] in decision_numbers]
    return record_list


def get_wage_determination_document(decision_number, modification_number):
    download_url = f'https://sam.gov/api/prod/wdol/v1/wd/{decision_number}/{modification_number}/download'
    print(f'Downloading wage determination document from {download_url}')
    response = http_client.get(download_url)
    return response.content


record_list = get_record_list()


print(f'Scraping {len(record_list)} documents')


for decision_number, modification_number, state, record in record_list:
    document_path = os.path.join('data', 'documents', state)
    document_filename = os.path.join(document_path, f'{decision_number}.{modification_number}.txt')
    if not os.path.exists(document_filename):
        os.makedirs(document_path, exist_ok=True)
        while True:
            try:
                document = get_wage_determination_document(decision_number, modification_number)
                with open(document_filename, 'wb') as document_file:
                    document_file.write(document)
                break
            except Exception as error:
                print(f'ERROR: {error}')
                time.sleep(60.0)
