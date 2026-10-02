import importlib.util
import json
from pathlib import Path


def generator():
    path = Path(__file__).resolve().parents[2] / 'scripts/generate_rebuild_contracts.py'
    spec = importlib.util.spec_from_file_location('trade_contract_generator', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_nullable_required_decimal_and_file_contracts_keep_distinct_types():
    module = generator()
    assert module.ts({'type': 'string', 'format': 'binary'}) == 'Blob'
    value = module.ts({'type': 'object', 'additionalProperties': False, 'required': ['amount'], 'properties': {
        'amount': {'anyOf': [{'type': 'string'}, {'type': 'null'}]},
        'parts': {'type': 'array', 'items': {'$ref': '#/components/schemas/Allocation'}},
    }})
    assert '"amount": (string | null)' in value and '"parts"?: Array<Components["Allocation"]>' in value
    assert module.ts({'type': 'object', 'additionalProperties': {'type': 'string'}}) == 'Record<string, string>'
    assert module.ts({}) == 'unknown'
    assert module.ts({'type': 'object'}) == 'Record<string, unknown>'
    assert module.ts({'type': 'object', 'additionalProperties': False}) == 'Record<string, never>'


def test_route_changes_and_schema_changes_both_change_fingerprint_without_inventing_shapes():
    module = generator()
    schema = {'paths': {'/api/v1/example': {'post': {
        'requestBody': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/Request'}}}},
        'responses': {'200': {'content': {'application/json': {'schema': {}}}}},
    }}}, 'components': {'schemas': {'Request': {'type': 'string'}}}}
    canonical, types = module.generate(schema)
    assert json.loads(canonical) == schema
    assert 'requestBody: Components["Request"]; response: unknown' in types
    assert 'query: Record<string, never>' in types
    schema['components']['schemas']['Request']['type'] = 'integer'
    assert module.generate(schema)[1] != types
    changed = module.generate(schema)[1]
    schema['paths']['/api/v1/moved'] = schema['paths'].pop('/api/v1/example')
    assert module.generate(schema)[1] != changed
