"""Authenticated API discovery; security declarations mirror route dependencies."""
from fastapi.routing import APIRoute
from . import assistant_api, bridge
from .security import COOKIE, require_user


def schema(app):
    result = app.openapi()
    schemes = result.setdefault('components', {}).setdefault('securitySchemes', {})
    schemes.update(SessionCookie={'type': 'apiKey', 'in': 'cookie', 'name': COOKIE},
                   TerminalToken={'type': 'http', 'scheme': 'bearer'},
                   BridgeToken={'type': 'http', 'scheme': 'bearer'})
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        dependencies = {d.call for d in route.dependant.dependencies}
        security = ([{'SessionCookie': []}, {'TerminalToken': []}]
                    if assistant_api.require_user_or_cli in dependencies else
                    [{'SessionCookie': []}] if require_user in dependencies else
                    [{'BridgeToken': []}] if bridge.require_bridge_key in dependencies else [])
        for method in route.methods:
            operation = result.get('paths', {}).get(route.path_format, {}).get(method.lower())
            if operation is not None:
                operation['security'] = security
    return result


@assistant_api.router.get('/openapi.json', include_in_schema=False)
def api_schema():
    from .main import app
    from fastapi.responses import JSONResponse
    return JSONResponse(schema(app), headers={'Cache-Control': 'no-store'})
