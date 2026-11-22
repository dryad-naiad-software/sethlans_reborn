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
    'ENUM_NAME_OVERRIDES': {
        # ``POST /api/jobs/{id}/release/`` body (Unreal foundation FR-RT-11).
        'JobReleaseReasonEnum': 'workers.views.job_release_actions.RELEASE_REASONS',
        # The worker's graphics card report (worker card selection FR-CR-7),
        # pinned so a later field with a similar name cannot rename them.
        'WorkerCardMethodEnum': 'workers.serializers.worker_card_schemas.CARD_METHODS',
        'WorkerCardGpuModeEnum': 'workers.serializers.worker_card_schemas.CARD_GPU_MODES',
        'WorkerCardStateEnum': 'workers.serializers.worker_card_schemas.CARD_STATES',
        'WorkerCardBackendEnum': 'workers.serializers.worker_card_schemas.CARD_BACKENDS',
        'WorkerCardAlertKindEnum': 'workers.serializers.worker_card_schemas.CARD_ALERT_KINDS',
        'WorkerCardNoticeKindEnum': 'workers.serializers.worker_card_schemas.CARD_NOTICE_KINDS',
        'WorkerCardOutcomeResultEnum': 'workers.serializers.worker_card_schemas.CARD_OUTCOME_RESULTS',
        'WorkerCardOutcomeCodeEnum': 'workers.serializers.worker_card_schemas.CARD_OUTCOME_CODES',
        # A project's and job's renderer and the renderer of a held card (Unreal
        # render per card FR-PC-MGR-3) share one value set, so one pinned name.
        'RendererEnum': 'workers.constants.Renderer',
        # The heartbeat ``gpu_memory.source`` names (gpu-memory-reader FR-GH-6).
        'WorkerGpuMemorySourceEnum': 'workers.serializers.workers.GPU_MEMORY_SOURCE_CHOICES',
        # What a pilot render ran with (gpu-memory-reader FR-GC-10): the
        # pilot-result body and the model.
        'UnrealGameOverridesEnum': 'shared.unreal.render_context.GAME_OVERRIDES_VALUES',
        'UnrealTextureStreamingEnum': 'shared.unreal.render_context.TEXTURE_STREAMING_VALUES',
        # Unreal sequence rendering (FR-SD-1, FR-SD-5): pinned beside the job status names.
        'SequenceDiscoveryStatusEnum': 'workers.constants_sequence.DiscoveryStatus',
        'SequenceShotSourceEnum': 'shared.unreal.shots_result.SHOT_SOURCES',
        'UnrealPresetKindEnum': 'shared.unreal.shots_result.PRESET_KINDS',
        # Submissions (FR-SQ-1, FR-SS-1): pinned for the same reason.
        'SequenceSubmissionStatusEnum': 'workers.constants_sequence.SequenceStatus',
        'SequenceShotStatusEnum': 'workers.constants_sequence.ShotStatus',
        'SequenceFrameFormatEnum': 'workers.constants_sequence.FrameFormat',
        # Audio job results (FR-SA-6, FR-SA-11).
        'SoundAppliedSourceEnum': 'workers.constants_sequence.SOUND_APPLIED_SOURCES',
        'SoundLatencySkippedEnum': 'workers.constants_sequence.SOUND_FLAG_VALUES',
        # Animation videos and the sequence result items (FR-SV-2) share one set.
        'VideoStatusEnum': 'workers.constants_video.VideoStatus',
        # A pilot's D6 verdict (Unreal per-machine memory test FR-MT-V-3, FR-MT-U-3).
        'MeasurementVerdictEnum': 'workers.serializers.unreal_measurement.PILOT_VERDICTS',
        # The memory report's spill cause (FR-MT-S-3) and D2's pair states: the report's
        # answer and the machine lists (FR-MT-U-1) share one value set, so one pinned name.
        'SpillCauseEnum': 'workers.serializers.memory_report.SPILL_CAUSES',
        'MachineStateEnum': 'workers.renderers.unreal_machine.PAIR_STATES',
    },
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
