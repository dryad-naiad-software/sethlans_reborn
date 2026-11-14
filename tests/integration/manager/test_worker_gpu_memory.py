# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: ``gpu_memory`` in registration and heartbeats (Unreal
foundation FR-RT-16, D8, FR-T-3, AC-35).
"""

import pytest

from workers.models import Worker

HB = '/api/heartbeat/'


def _gpu(**extra):
    gpu = {'index': 0, 'name': 'NVIDIA GeForce RTX 5090', 'total_mb': 32607, 'used_mb': 1012,
           'sethlans_used_mb': 0, 'other_used_mb': 1012}
    gpu.update(extra)
    return gpu


def _mem(*gpus, **extra):
    mem = {'schema': 1, 'source': 'nvidia-smi', 'age_s': 4, 'gpus': list(gpus) or [_gpu()]}
    mem.update(extra)
    return mem


def _post(client, hostname, **extra):
    resp = client.post(HB, {'hostname': hostname, **extra}, format='json')
    assert resp.status_code == 200, resp.data
    return resp


def _stored(worker):
    return Worker.objects.get(pk=worker.pk).gpu_memory


@pytest.mark.django_db
class TestGpuMemory:

    def test_valid_payload_is_stored_and_returned(self, make_worker_client, admin_client):
        worker, client, _token = make_worker_client('gpu-valid')
        resp = _post(client, 'gpu-valid', gpu_memory=_mem())
        assert resp.data['gpu_memory'] == _mem()
        assert _stored(worker) == _mem()
        row = next(w for w in admin_client.get(HB).data if w['hostname'] == 'gpu-valid')
        assert row['gpu_memory'] == _mem()

    @pytest.mark.parametrize('mem', [
        _mem(schema=2), _mem(source='rocm'), _mem(age_s=86401), _mem(*[_gpu(index=i) for i in range(17)]),
        _mem(schema=True),
    ])
    def test_bad_top_level_is_stored_empty(self, make_worker_client, mem):
        worker, client, _token = make_worker_client('gpu-top')
        _post(client, 'gpu-top', gpu_memory=mem)
        assert _stored(worker) == {}

    @pytest.mark.parametrize('gpus, kept', [
        ([_gpu(), _gpu(name='dup')], 1),
        ([_gpu(total_mb=1_048_577)], 0),
        ([_gpu(used_mb=40000)], 0),
        ([_gpu(index=True)], 0),
    ])
    def test_bad_gpus_are_dropped(self, make_worker_client, gpus, kept):
        worker, client, _token = make_worker_client('gpu-drop')
        _post(client, 'gpu-drop', gpu_memory=_mem(*gpus))
        assert len(_stored(worker)['gpus']) == kept

    def test_bad_attribution_is_nulled(self, make_worker_client):
        worker, client, _token = make_worker_client('gpu-attr')
        _post(client, 'gpu-attr', gpu_memory=_mem(_gpu(sethlans_used_mb=30000, other_used_mb=3000)))
        gpu = _stored(worker)['gpus'][0]
        assert (gpu['total_mb'], gpu['sethlans_used_mb'], gpu['other_used_mb']) == (32607, None, None)

    def test_heartbeat_without_the_key_keeps_the_value(self, make_worker_client):
        worker, client, _token = make_worker_client('gpu-keep')
        Worker.objects.filter(pk=worker.pk).update(gpu_memory=_mem())
        _post(client, 'gpu-keep')
        assert _stored(worker) == _mem()

    def test_registration_without_the_key_stores_empty(self, make_worker_client):
        worker, client, _token = make_worker_client('gpu-reg')
        Worker.objects.filter(pk=worker.pk).update(gpu_memory=_mem())
        _post(client, 'gpu-reg', os='Windows')
        assert _stored(worker) == {}
