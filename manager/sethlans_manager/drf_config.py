# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

# --- DRF Configuration ---
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.TokenAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    # FR-8 / FR-8a: unified error envelope scoped to /api/setup/*.
    'EXCEPTION_HANDLER': 'workers.utils.errors.setup_exception_handler',
}

# --- DRF Spectacular Configuration ---
SPECTACULAR_SETTINGS = {
    'TITLE': 'Sethlans Reborn API',
    'DESCRIPTION': (
        'RESTful API for the distributed Blender rendering system.'
    ),
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'SECURITY': [
        {'sessionAuth': []},
        {'tokenAuth': []},
    ],
    'APPEND_COMPONENTS': {
        'securitySchemes': {
            'sessionAuth': {
                'type': 'apiKey',
                'in': 'cookie',
                'name': 'sessionid',
            },
            'tokenAuth': {
                'type': 'apiKey',
                'in': 'header',
                'name': 'Authorization',
                'description': (
                    'Token-based auth. Format: "Token <api_token>"'
                ),
            },
        },
        # NFR-10: unified error envelope referenced from 4xx responses
        # of every /api/setup/* operation.
        'schemas': {
            'ErrorEnvelope': {
                'type': 'object',
                'required': ['error'],
                'properties': {
                    'error': {
                        'type': 'object',
                        'required': ['code', 'message', 'details'],
                        'properties': {
                            'code': {
                                'type': 'string',
                                'enum': [
                                    'invalid_token',
                                    'rate_limited',
                                    'setup_complete',
                                    'setup_in_progress',
                                    'precondition_unmet',
                                    'setup_session_conflict',
                                    'invalid_input',
                                    'internal_error',
                                ],
                            },
                            'message': {'type': 'string'},
                            'details': {
                                'type': 'object',
                                'additionalProperties': True,
                            },
                        },
                    },
                },
            },
        },
    },
}
