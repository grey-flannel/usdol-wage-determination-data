import cachier
import httpx
import orjson
import us


http_client = httpx.Client()


states = [s.abbr for s in us.STATES_AND_TERRITORIES]


@cachier.cachier()
def search_wage_determinations(params):
    search_url = 'https://sam.gov/api/prod/sgs/v1/search'
    response = http_client.get(search_url, params=params)
    return response.json()


def get_wage_determination_index():
    print('Downloading wage determination index')
    index = {'records': []}
    for state in states:
        params = {'index': 'dbra', 'state': state, 'page': 0, 'size': 1000, 'sort': 'title'}
        while True:
            print('Downloading page {page} for state {state}'.format(**params))
            body = search_wage_determinations(params)
            records = body.get('_embedded', {}).get('results', [])
            if records:
                print(f'Downloaded {len(records)} records')
                index['records'].extend(records)
                params['page'] += 1
            else:
                break
    return index


index = get_wage_determination_index()
record_count = len(index['records'])

with open('data/index.json', 'wb') as index_file:
    index_file.write(orjson.dumps(index, option=orjson.OPT_INDENT_2))

print(f'Saved {record_count} total records to data/index.json')
