import orjson


with open('data/index.json') as records_file:
    index = orjson.loads(records_file.read())

records = index['records']

record_map = {r['fullReferenceNumber'].upper(): r for r in records}


construction_types = set(t for r in records for t in r.get('constructionTypes', []))

for t in construction_types:
    print(t)
print()


with open('data/priority-decision-numbers.txt') as numbers_file:
    priority_decision_numbers = [n.strip() for n in numbers_file]

print(','.join(priority_decision_numbers))


for number in priority_decision_numbers:
    # print(number)
    # print(record_map[number])
    # print()
    record = record_map.get(number, {})
    print(number, record.get('isActive', None))
