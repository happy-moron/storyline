import pytest
from unittest.mock import patch, MagicMock

from storyline.services import ServiceManager, ServiceStatus
from storyline.services.config import ServiceConfig


@pytest.fixture(autouse=True)
def reset_singleton():
    ServiceManager._instance = None
    ServiceManager._active_service = None
    yield
    ServiceManager._instance = None
    ServiceManager._active_service = None

@pytest.fixture
def manager(reset_singleton):  # explicit dependency ensures ordering
    return ServiceManager()

class TestServiceManager:
    def test_singleton(self):
        m1 = ServiceManager()
        m2 = ServiceManager()
        assert m1 is m2

    def test_get_config(self, manager):
        config = manager.get_config('llm')
        assert isinstance(config, ServiceConfig)
        assert config.name == 'llamacpp'
        assert config.port == 11432

    def test_get_config_unknown(self, manager):
        with pytest.raises(ValueError, match="Unknown service"):
            manager.get_config('unknown')

    def test_get_status_offline(self, manager):
        mock_result = MagicMock()
        mock_result.returncode = 3
        with patch.object(manager, '_subprocess_run', return_value=mock_result):
            status = manager.get_status('llm')
            assert status == ServiceStatus.OFFLINE

    def test_get_status_online(self, manager):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch.object(manager, '_subprocess_run', return_value=mock_result):
            status = manager.get_status('llm')
            assert status == ServiceStatus.ONLINE

    def test_is_service_running_true(self, manager):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch.object(manager, '_subprocess_run', return_value=mock_result):
            assert manager._is_service_running('llm')

    def test_is_service_running_false(self, manager):
        mock_result = MagicMock()
        mock_result.returncode = 3
        with patch.object(manager, '_subprocess_run', return_value=mock_result):
            assert not manager._is_service_running('llm')

    def test_is_service_running_timeout(self, manager):
        with patch.object(manager, '_subprocess_run', side_effect=TimeoutError()):
            assert not manager._is_service_running('llm')

    def test_is_service_running_exception(self, manager):
        with patch.object(manager, '_subprocess_run', side_effect=Exception('test')):
            assert not manager._is_service_running('llm')

    def test_is_service_running_exception_handling(self, manager):
        with patch.object(manager, '_subprocess_run', side_effect=Exception('test')):
            assert not manager._is_service_running('llm')

    def test_is_service_enabled_true(self, manager):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch.object(manager, '_subprocess_run', return_value=mock_result):
            assert manager._is_service_enabled('llm')

    def test_is_service_enabled_false(self, manager):
        mock_result = MagicMock()
        mock_result.returncode = 1
        with patch.object(manager, '_subprocess_run', return_value=mock_result):
            assert not manager._is_service_enabled('llm')

    def test_start_service_success(self, manager):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch.object(manager, '_subprocess_run', return_value=mock_result):
            assert manager._start_service('llm')

    def test_start_service_failure(self, manager):
        with patch.object(manager, '_subprocess_run', side_effect=RuntimeError()):
            assert not manager._start_service('llm')

    def test_stop_service_success(self, manager):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch.object(manager, '_subprocess_run', return_value=mock_result):
            assert manager._stop_service('llm')

    def test_stop_service_failure(self, manager):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch.object(manager, '_subprocess_run',
                        side_effect=[mock_result, RuntimeError()]):
            with pytest.raises(RuntimeError, match="Failed to stop service"):
                manager.stop('llm')

    def test_wait_for_service_online(self, manager):
        with patch('requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response
            assert manager._wait_for_service('llm')

    def test_wait_for_service_timeout(self, manager):
        with patch('time.time', side_effect=[0, 0, 200]):
            with patch('requests.get', side_effect=Exception('test')):
                assert not manager._wait_for_service('llm')

    def test_wait_for_service_offline(self, manager):
        mock_result1 = MagicMock()
        mock_result1.returncode = 0
        mock_result2 = MagicMock()
        mock_result2.returncode = 3
        with patch.object(manager, '_subprocess_run') as mock_run:
            mock_run.side_effect = [mock_result1, mock_result2]
            assert manager._wait_for_service_offline('llm')

    def test_wait_for_service_offline_timeout(self, manager):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch('time.time', side_effect=[0, 0, 200]):
            with patch.object(manager, '_subprocess_run', return_value=mock_result):
                assert not manager._wait_for_service_offline('llm')

    def test_start_if_needed_online(self, manager):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch.object(manager, '_subprocess_run', return_value=mock_result):
            assert manager.start('llm')
            assert manager.get_status('llm') == ServiceStatus.ONLINE

    def test_start_with_other_active(self, manager):
        manager._active_service = 'tts'
        gpu_free_result = MagicMock()
        gpu_free_result.stdout = '5000'
        results = [
            MagicMock(returncode=0),  # get_status('tts') → ONLINE in stop()
            MagicMock(returncode=0),  # _stop_service('tts')
            MagicMock(returncode=3),  # _wait_for_service_offline → OFFLINE
            gpu_free_result,          # _wait_for_gpu_memory: nvidia-smi showing 5000 MiB free
            MagicMock(returncode=3),  # get_status('llm') → OFFLINE in start()
            MagicMock(returncode=0),  # _start_service('llm')
        ]
        with patch.object(manager, '_subprocess_run', side_effect=results):
            with patch('requests.get') as mock_get:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_get.return_value = mock_response
                assert manager.start('llm')
                assert manager.get_active_service() == 'llm'

    def test_start_service_failure(self, manager):
        with patch.object(manager, '_subprocess_run', side_effect=RuntimeError()):
            with pytest.raises(RuntimeError, match="Failed to start service"):
                manager.start('llm')

    def test_start_timeout(self, manager):
        mock_result = MagicMock()
        mock_result.returncode = 3
        with patch('time.time', side_effect=[0, 0, 200, 200, 200]):
            with patch('time.sleep'):
                with patch.object(manager, '_subprocess_run', return_value=mock_result):
                    with patch('requests.get', side_effect=Exception('test')):
                        with pytest.raises(RuntimeError, match="failed to start within timeout"):
                            manager.start('llm')

    def test_stop_already_offline(self, manager):
        assert manager.stop('llm')

    def test_stop_timeout(self, manager):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch.object(manager, '_subprocess_run', return_value=mock_result):
            with pytest.raises(RuntimeError, match="failed to stop within timeout"):
                manager.stop('llm')

    def test_get_active_service(self, manager):
        mock_offline = MagicMock()
        mock_offline.returncode = 3
        
        mock_online = MagicMock()
        mock_online.returncode = 0
        
        with patch.object(manager, '_subprocess_run', side_effect=[mock_offline, mock_offline, mock_offline]):
            assert manager.get_active_service() is None

        with patch.object(manager, '_subprocess_run', side_effect=[mock_offline, mock_offline, mock_online]):
            assert manager.get_active_service() == 'image_gen'

        with patch.object(manager, '_subprocess_run', side_effect=[mock_offline, mock_online, mock_offline]):
            assert manager.get_active_service() == 'tts'

        with patch.object(manager, '_subprocess_run', side_effect=[mock_online, mock_offline, mock_offline]):
            assert manager.get_active_service() == 'llm'

    def test_start_if_needed_online(self, manager):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch.object(manager, '_subprocess_run', return_value=mock_result):
            assert manager.start_if_needed('llm')

    def test_start_if_needed_offline(self, manager):
        mock_result = MagicMock()
        mock_result.returncode = 3  # OFFLINE
        with patch.object(manager, '_subprocess_run', return_value=mock_result):
            with patch('requests.get') as mock_get:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_get.return_value = mock_response
                assert manager.start_if_needed('llm')

    def test_stop_if_running_offline(self, manager):
        assert manager.stop_if_running('llm')

    def test_stop_if_running_online(self, manager):
        with patch.object(manager, '_subprocess_run', side_effect=[
            MagicMock(returncode=0),  # get_status → ONLINE
            MagicMock(returncode=0),  # _stop_service
            MagicMock(returncode=3),  # _wait_for_service_offline → OFFLINE
        ]):
            assert manager.stop_if_running('llm')

    def test_start_all(self, manager):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch.object(manager, '_subprocess_run', return_value=mock_result):
            with patch('requests.get') as mock_get:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_get.return_value = mock_response
                results = manager.start_all()
                assert results['llm'] is True
                assert results['tts'] is True
                assert results['image_gen'] is True

    def test_stop_all(self, manager):
        with patch.object(manager, '_subprocess_run', side_effect=[
            MagicMock(returncode=0),  # llm: get_status → ONLINE
            MagicMock(returncode=0),  # llm: _stop_service
            MagicMock(returncode=3),  # llm: wait offline → OFFLINE
            MagicMock(returncode=0),  # tts: get_status → ONLINE
            MagicMock(returncode=0),  # tts: _stop_service
            MagicMock(returncode=3),  # tts: wait offline → OFFLINE
            MagicMock(returncode=0),  # image_gen: get_status → ONLINE
            MagicMock(returncode=0),  # image_gen: _stop_service
            MagicMock(returncode=3),  # image_gen: wait offline → OFFLINE
        ]):
            results = manager.stop_all()
            assert results['llm'] is True
            assert results['tts'] is True
            assert results['image_gen'] is True

    def test_ensure_only_one_active_multiple(self, manager):
        mock_result = MagicMock()
        mock_result.returncode = 0
        
        with patch.object(manager, '_subprocess_run', return_value=mock_result):
            with pytest.raises(RuntimeError, match="Multiple services are active"):
                manager.ensure_only_one_active()

    def test_ensure_only_one_active_no_active(self, manager):
        mock_result_offline = MagicMock()
        mock_result_offline.returncode = 3
        
        with patch.object(manager, '_subprocess_run', return_value=mock_result_offline):
            assert manager.ensure_only_one_active() is None

    def test_ensure_only_one_active_single_active(self, manager):
        mock_offline = MagicMock()
        mock_offline.returncode = 3
        
        mock_online = MagicMock()
        mock_online.returncode = 0
        
        with patch.object(manager, '_subprocess_run', side_effect=[mock_offline, mock_offline, mock_online]):
            assert manager.ensure_only_one_active() == 'image_gen'

        with patch.object(manager, '_subprocess_run', side_effect=[mock_offline, mock_online, mock_offline]):
            assert manager.ensure_only_one_active() == 'tts'

        with patch.object(manager, '_subprocess_run', side_effect=[mock_online, mock_offline, mock_offline]):
            assert manager.ensure_only_one_active() == 'llm'
