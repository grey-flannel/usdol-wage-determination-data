import orjson

from usdol_wage_determination_model import WageDetermination


json_schema = {
    '$schema': 'https://json-schema.org/draft/2020-12/schema',
    '$id': 'https://greyflannelconsulting.com/schemas/wage-determination.json',
}

json_schema.update(WageDetermination.model_json_schema())


with open('schema/wage-determination.json', 'wb') as schema_file:
    schema_file.write(orjson.dumps(json_schema, option=orjson.OPT_INDENT_2))
