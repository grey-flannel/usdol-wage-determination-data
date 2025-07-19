import cachier
import httpx


http_client = httpx.Client()


@cachier.cachier()
def get_wage_determination_record(decision_number, modification_number):
    api_url = f'https://sam.gov/api/prod/wdol/v1/wd/{decision_number}/{modification_number}'
    response = http_client.get(api_url)
    try:
        return response.json()
    except Exception:
        return None


record = get_wage_determination_record('TX20250249', 1)

print(record['document'][:-1])
del record['document']
print(record)
