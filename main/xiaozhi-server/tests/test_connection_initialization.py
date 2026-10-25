"""Connection startup regressions using real lifecycle and hello code.

Run directly: python -B tests/test_connection_initialization.py -v
The pytest conftest replaces asyncio.sleep globally, so these timing tests
intentionally use standalone unittest. Provider/API/plugin boundaries are local
fakes; no deployment config, model, device or network is accessed.
"""
__test__ = False

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import queue
import sys
import threading
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, **attrs):
    result = types.ModuleType(name)
    result.__dict__.update(attrs)
    return result


class DeviceNotFound(Exception):
    pass


class DeviceNeedsBinding(Exception):
    def __init__(self, code):
        self.bind_code = code


class SocketClosed(Exception):
    pass


class FakeSocket:
    """A WebSocket transport whose close notification is independently awaitable."""

    def __init__(self):
        self.closed = False
        self.closed_event = asyncio.Event()
        self.sent = []
        self.state = types.SimpleNamespace(name="OPEN")

    async def send(self, value):
        self.sent.append(json.loads(value))

    async def close(self, code=1000, reason=""):
        self.close_code = code
        self.closed = True
        self.state.name = "CLOSED"
        self.closed_event.set()

    async def wait_closed(self):
        await self.closed_event.wait()


class Runtime:
    def __init__(self):
        self.logger = Mock()
        self.logger.bind.return_value = self.logger
        self.dispatched = []
        self.bind_prompts = []
        self.prompt_received = asyncio.Event()
        self.api = AsyncMock(return_value={"selected_module": {}})
        self.providers = Mock(return_value={})
        util_names = (
            "extract_json_from_string", "check_vad_update", "check_asr_update",
            "filter_sensitive_info", "get_system_error_response", "audio_to_data",
            "audio_file_to_wav_bytes", "remove_punctuation_and_length",
            "opus_datas_to_wav_bytes",
        )
        util = {name: Mock(return_value=None) for name in util_names}
        util["check_vad_update"] = Mock(return_value=False)
        util["check_asr_update"] = Mock(return_value=False)
        util["filter_sensitive_info"] = lambda value: value
        self.stubs = {
            "numpy": module("numpy"),
            "opuslib_next": module("opuslib_next"),
            "websockets": module("websockets", ServerConnection=FakeSocket,
                                 exceptions=types.SimpleNamespace(ConnectionClosed=SocketClosed)),
            "core.utils.util": module("core.utils.util", **util),
            "core.utils.modules_initialize": module("core.utils.modules_initialize",
                initialize_modules=self.providers, initialize_tts=Mock(), initialize_asr=Mock()),
            "core.handle.reportHandle": module("core.handle.reportHandle", report=AsyncMock(), enqueue_tool_report=Mock()),
            "core.providers.tts.default": module("core.providers.tts.default", DefaultTTS=Mock()),
            "core.utils.dialogue": module("core.utils.dialogue", Message=Mock(), Dialogue=Mock()),
            "core.providers.asr.dto.dto": module("core.providers.asr.dto.dto", InterfaceType=Mock()),
            "core.handle.textHandle": module("core.handle.textHandle", handleTextMessage=self.dispatch),
            "core.providers.tools.unified_tool_handler": module("core.providers.tools.unified_tool_handler", UnifiedToolHandler=Mock()),
            "plugins_func.loadplugins": module("plugins_func.loadplugins", auto_import_modules=Mock()),
            "plugins_func.register": module("plugins_func.register", Action=Mock(), ActionResponse=Mock(), all_function_registry={}, module_func_map={}),
            "core.auth": module("core.auth", AuthenticationError=type("AuthenticationError", (Exception,), {})),
            "config.config_loader": module("config.config_loader", get_private_config_from_api=self.api),
            "config.manage_api_client": module("config.manage_api_client", DeviceNotFoundException=DeviceNotFound,
                DeviceBindException=DeviceNeedsBinding, generate_and_save_chat_title=AsyncMock()),
            "config.logger": module("config.logger", setup_logging=lambda: self.logger,
                build_module_string=lambda _: "test", create_connection_logger=lambda _: self.logger),
            "core.utils.prompt_manager": module("core.utils.prompt_manager", PromptManager=Mock()),
            "core.utils.voiceprint_provider": module("core.utils.voiceprint_provider", VoiceprintProvider=Mock()),
            "core.utils.textUtils": module("core.utils.textUtils"),
            "core.providers.tts.dto.dto": module("core.providers.tts.dto.dto", ContentType=Mock(), TTSMessageDTO=Mock(),
                SentenceType=types.SimpleNamespace(FIRST="FIRST", MIDDLE="MIDDLE", LAST="LAST")),
            "core.utils.wakeup_word": module("core.utils.wakeup_word", WakeupWordsConfig=Mock()),
            "core.handle.sendAudioHandle": module("core.handle.sendAudioHandle", sendAudioMessage=AsyncMock(), send_tts_message=AsyncMock()),
            "core.providers.tools.device_mcp": module("core.providers.tools.device_mcp", MCPClient=Mock(), send_mcp_initialize_message=AsyncMock()),
            "core.handle.receiveAudioHandle": module("core.handle.receiveAudioHandle", check_bind_device=self.prompt),
        }
        self.patch = patch.dict(sys.modules, self.stubs)
        self.patch.start()
        sys.path.insert(0, str(ROOT))
        self.connection = self.load("_connection_under_test", Path(os.environ.get("CONNECTION_UNDER_TEST", ROOT / "core/connection.py")))
        self.hello = self.load("_hello_under_test", ROOT / "core/handle/helloHandle.py")

    @staticmethod
    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        result = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(result)
        return result

    def stop(self):
        self.patch.stop()
        sys.path.remove(str(ROOT))

    async def dispatch(self, conn, message):
        self.dispatched.append(message)
        payload = json.loads(message)
        if payload.get("type") == "hello":
            await self.hello.handleHelloMessage(conn, payload)

    async def prompt(self, conn):
        # A real binding prompt dereferences this queue. Fail explicitly if the
        # lifecycle invokes the handler before TTS is available.
        assert conn.tts is not None, "binding prompt ran before TTS initialization"
        assert conn.tts.opened, "binding prompt ran before TTS channel opened"
        self.bind_prompts.append(conn.bind_code)
        self.prompt_received.set()

    def new_connection(self):
        config = {
            "read_config_from_api": True,
            "exit_commands": [],
            "selected_module": {},
            "xiaozhi": {"type": "hello", "transport": "websocket", "audio_params": {"sample_rate": 24000}},
        }
        conn = self.connection.ConnectionHandler(config, None, None, None, None, None)
        conn.loop = asyncio.get_running_loop()
        conn.headers = {"device-id": "02:00:00:00:00:01", "client-id": "test-client"}
        conn.websocket = FakeSocket()
        conn.welcome_msg = dict(config["xiaozhi"], session_id=conn.session_id)
        return conn


class ConnectionInitializationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = Runtime()
        self.addCleanup(self.runtime.stop)
        self.conn = self.runtime.new_connection()

    async def asyncTearDown(self):
        await self.conn.close()

    def start_initialization(self, already_hello=True):
        if already_hello:
            self.conn._hello_received_event.set()
        task = asyncio.create_task(self.conn._background_initialize())
        self.conn._initialization_task = task
        return task

    async def test_hello_survives_slow_initialization_and_listen_waits(self):
        release_api = asyncio.Event()
        api_started = asyncio.Event()

        async def slow_api(*args):
            api_started.set()
            await release_api.wait()
            return {"selected_module": {}}

        self.runtime.api.side_effect = slow_api
        self.conn._initialize_components = AsyncMock()
        initialization = self.start_initialization(already_hello=False)
        await api_started.wait()
        hello = json.dumps({"type": "hello", "version": 1, "transport": "websocket",
                            "audio_params": {"format": "opus", "sample_rate": 16000,
                                             "channels": 1, "frame_duration": 60}})
        await asyncio.wait_for(self.conn._route_message(hello), timeout=0.25)
        self.assertEqual(len(self.conn.websocket.sent), 1)
        self.assertEqual(self.conn.websocket.sent[0]["type"], "hello")
        self.assertEqual(self.conn.websocket.sent[0]["audio_params"]["sample_rate"], 16000)
        listen = json.dumps({"type": "listen", "state": "start", "mode": "auto"})
        pending_listen = asyncio.create_task(self.conn._route_message(listen))
        # Exceed the old one-second binding deadline using the real event loop.
        await asyncio.sleep(1.1)
        self.assertFalse(pending_listen.done())
        self.assertEqual(self.runtime.bind_prompts, [])
        release_api.set()
        await asyncio.wait_for(initialization, 1)
        await asyncio.wait_for(pending_listen, 1)
        self.assertEqual(self.runtime.dispatched, [hello, listen])
        self.assertEqual(len(self.conn.websocket.sent), 1)

    async def test_mcp_response_is_not_blocked_by_component_initialization(self):
        message = json.dumps({"type": "mcp", "payload": {"jsonrpc": "2.0", "id": 1,
                                                         "result": {"tools": []}}})
        await asyncio.wait_for(self.conn._route_message(message), 0.25)
        self.assertEqual(self.runtime.dispatched, [message])
        self.assertEqual(self.runtime.bind_prompts, [])

    async def test_audio_waits_for_ready_then_reaches_decoder_once(self):
        self.conn.vad = Mock()
        self.conn.asr = types.SimpleNamespace(close=AsyncMock())
        self.conn._decode_opus_packet = Mock(return_value=b"decoded-pcm")
        route = asyncio.create_task(self.conn._route_message(b"opus-packet"))
        await asyncio.sleep(0.02)
        self.conn._decode_opus_packet.assert_not_called()
        self.conn.initialization_completed_event.set()
        await asyncio.wait_for(route, 0.5)
        self.conn._decode_opus_packet.assert_called_once_with(b"opus-packet")
        self.assertEqual(self.conn.asr_audio_queue.get_nowait(), b"decoded-pcm")

    async def test_unbound_prompt_waits_until_tts_channel_is_open(self):
        open_started = asyncio.Event()
        finish_open = asyncio.Event()
        tts = types.SimpleNamespace(opened=False, close=AsyncMock(),
                                    tts_text_queue=queue.Queue(), tts_audio_queue=queue.Queue())

        async def open_channel(conn):
            open_started.set()
            await finish_open.wait()
            tts.opened = True

        tts.open_audio_channels = open_channel
        self.conn._initialize_tts = Mock(return_value=tts)
        self.runtime.api.side_effect = DeviceNeedsBinding("123456")
        initialization = self.start_initialization()
        await asyncio.wait_for(open_started.wait(), 0.5)
        route = asyncio.create_task(self.conn._route_message('{"type":"listen","state":"start"}'))
        await asyncio.sleep(0.03)
        self.assertEqual(self.runtime.bind_prompts, [])
        self.assertFalse(route.done())
        finish_open.set()
        await asyncio.wait_for(initialization, 0.5)
        await asyncio.wait_for(route, 0.5)
        await asyncio.wait_for(self.runtime.prompt_received.wait(), 0.5)
        self.assertEqual(self.runtime.bind_prompts, ["123456"])
        self.assertEqual(self.runtime.dispatched, [])

    async def test_api_failure_closes_with_error_without_binding_prompt(self):
        self.runtime.api.side_effect = RuntimeError("synthetic manager failure")
        self.conn._initialize_components = AsyncMock()
        await self.start_initialization()
        await asyncio.wait_for(self.conn._route_message('{"type":"listen","state":"start"}'), 0.5)
        self.assertIsNotNone(self.conn.initialization_error)
        self.assertFalse(self.conn.need_bind)
        self.assertTrue(self.conn.websocket.closed)
        self.assertEqual(self.conn.websocket.close_code, 1011)
        self.assertEqual(self.runtime.bind_prompts, [])
        self.conn._initialize_components.assert_not_awaited()

    async def test_component_failure_does_not_report_device_missing(self):
        self.conn._initialize_components = AsyncMock(side_effect=RuntimeError("synthetic TTS failure"))
        await self.start_initialization()
        await asyncio.wait_for(self.conn._route_message('{"type":"listen","state":"start"}'), 0.5)
        self.assertIsNotNone(self.conn.initialization_error)
        self.assertFalse(self.conn.need_bind)
        self.assertEqual(self.runtime.bind_prompts, [])
        self.assertEqual(self.conn.websocket.close_code, 1011)

    async def test_close_cancels_pending_api_before_executor_is_released(self):
        api_started = asyncio.Event()
        api_cancelled = asyncio.Event()

        async def hanging_api(*args):
            api_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                api_cancelled.set()
                raise

        self.runtime.api.side_effect = hanging_api
        self.conn._initialize_components = AsyncMock()
        initialization = self.start_initialization()
        await api_started.wait()
        route = asyncio.create_task(self.conn._route_message('{"type":"listen","state":"start"}'))
        await asyncio.sleep(0)
        await asyncio.wait_for(self.conn.close(), 0.5)
        await asyncio.wait_for(route, 0.5)
        self.assertTrue(initialization.done())
        self.assertTrue(api_cancelled.is_set())
        self.assertIsNone(self.conn.executor)
        self.conn._initialize_components.assert_not_awaited()
        self.runtime.providers.assert_not_called()
        self.assertEqual(self.runtime.bind_prompts, [])

    async def test_empty_manager_response_fails_instead_of_using_shared_configuration(self):
        # config_loader maps some API failures to {}, which is not a device config.
        self.runtime.api.return_value = {}
        self.conn._initialize_components = AsyncMock()
        await self.start_initialization()
        self.assertIsNotNone(self.conn.initialization_error)
        self.assertFalse(self.conn.need_bind)
        self.assertEqual(self.conn.websocket.close_code, 1011)
        self.conn._initialize_components.assert_not_awaited()

    async def test_correction_words_without_device_models_is_an_initialization_error(self):
        # get_private_config_from_api may preserve a successful correction-words
        # request while its concurrent agent-models request failed.
        self.runtime.api.return_value = {"correct_words": [{"original": "a", "target": "b"}]}
        self.conn._initialize_components = AsyncMock()
        await self.start_initialization()
        self.assertIsNotNone(self.conn.initialization_error)
        self.assertFalse(self.conn.need_bind)
        self.assertEqual(self.conn.websocket.close_code, 1011)
        self.runtime.providers.assert_not_called()
        self.conn._initialize_components.assert_not_awaited()

    async def test_cancelled_mcp_initialization_cleans_unregistered_worker(self):
        started = asyncio.Event()
        shutdown = asyncio.Event()
        created_clients = []

        class PendingClient:
            def __init__(self, config):
                self.worker = None
                self.cleanup = AsyncMock(side_effect=self.finish)
                created_clients.append(self)

            async def initialize(self, **kwargs):
                self.worker = asyncio.create_task(shutdown.wait())
                started.set()
                await asyncio.Future()

            async def finish(self):
                shutdown.set()
                await self.worker

        fake_types = module("mcp.types", LoggingMessageNotificationParams=Mock())
        fake_client = module("core.providers.tools.server_mcp.mcp_client", ServerMCPClient=PendingClient)
        loader = self.runtime.stubs["config.config_loader"]
        with patch.object(loader, "get_project_dir", return_value="/nonexistent-test-root/", create=True), patch.dict(sys.modules, {
            "mcp.types": fake_types,
            "core.providers.tools.server_mcp.mcp_client": fake_client,
        }):
            manager_module = self.runtime.load("core.providers.tools.server_mcp._manager_under_test",
                                               ROOT / "core/providers/tools/server_mcp/mcp_manager.py")
            manager = manager_module.ServerMCPManager(self.conn)
            initialization = asyncio.create_task(manager._init_server("local-fake", {"url": "http://offline.invalid/mcp"}))
            await asyncio.wait_for(started.wait(), 0.5)
            self.assertEqual(manager.clients, {})
            initialization.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(initialization, 0.5)
            self.assertEqual(len(created_clients), 1)
            client = created_clients[0]
            client.cleanup.assert_awaited_once()
            self.assertTrue(client.worker.done())
            self.assertEqual(manager.clients, {})
            self.assertEqual(manager.tools, [])

    async def test_initialization_deadline_cancels_work_and_releases_waiters(self):
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def hanging_api(*args):
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        self.runtime.api.side_effect = hanging_api
        with patch.object(self.runtime.connection, "INITIALIZATION_TIMEOUT_SECONDS", 0.05):
            initialization = self.start_initialization()
            await entered.wait()
            route = asyncio.create_task(self.conn._route_message('{"type":"listen","state":"start"}'))
            await asyncio.wait_for(initialization, 0.5)
            await asyncio.wait_for(route, 0.5)
        self.assertTrue(cancelled.is_set())
        self.assertIsNotNone(self.conn.initialization_error)
        self.assertEqual(self.conn.websocket.close_code, 1011)
        self.assertEqual(self.runtime.bind_prompts, [])

    async def test_late_provider_result_is_not_attached_after_disconnect(self):
        creation_started = asyncio.Event()
        creation_finished = threading.Event()
        finish_creation = threading.Event()
        loop = asyncio.get_running_loop()
        late_tts = types.SimpleNamespace(open_audio_channels=AsyncMock(), close=AsyncMock())

        def slow_provider(*args):
            loop.call_soon_threadsafe(creation_started.set)
            finish_creation.wait(2)
            creation_finished.set()
            return {"tts": late_tts}

        self.runtime.providers.side_effect = slow_provider
        self.conn._initialize_components = AsyncMock()
        self.start_initialization()
        try:
            await asyncio.wait_for(creation_started.wait(), 0.5)
            await asyncio.wait_for(self.conn.close(), 0.5)
        finally:
            finish_creation.set()
            await asyncio.to_thread(creation_finished.wait, 2)
        await asyncio.sleep(0)
        self.assertIsNone(self.conn.tts)
        self.assertIsNone(self.conn.executor)
        late_tts.open_audio_channels.assert_not_awaited()
        self.conn._initialize_components.assert_not_awaited()

    async def test_peer_close_releases_waiting_business_message(self):
        route = asyncio.create_task(self.conn._route_message('{"type":"listen","state":"start"}'))
        await asyncio.sleep(0)
        await self.conn.websocket.close()
        await asyncio.wait_for(route, 0.5)
        self.assertEqual(self.runtime.dispatched, [])
        self.assertEqual(self.runtime.bind_prompts, [])


if __name__ == "__main__":
    unittest.main()
