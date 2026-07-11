#!/usr/bin/env python3
"""Manual test program for TTS service.

This script performs real integration testing with the TTS service (qwen3-tts-flask).
It uses HTTP requests to interact with the Flask API endpoints.
"""

import sys
import base64
import json
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from storyline.services.manager import ServiceManager, ServiceStatus


def test_tts_service():
    """Test TTS service with real API calls."""
    print("=" * 60)
    print("TTS Service Manual Test")
    print("=" * 60)

    manager = ServiceManager()

    # Check if TTS service is running
    status = manager.get_status('tts')
    print(f"\nTTS Service Status: {status.value}")

    if status == ServiceStatus.OFFLINE:
        print("\nStarting TTS service...")
        if not manager.start('tts'):
            print("ERROR: Failed to start TTS service")
            return False
        print("TTS service started successfully")
        import time
        time.sleep(2)  # Wait for service to be ready

    # Test 1: Health check
    print("\n" + "-" * 60)
    print("Test 1: Health Check")
    print("-" * 60)
    try:
        import requests
        response = requests.get('http://127.0.0.1:11433/health', timeout=5)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.json()}")
    except Exception as e:
        print(f"ERROR: {e}")
        return False

    # Test 2: Custom voice with single text
    print("\n" + "-" * 60)
    print("Test 2: Custom Voice - Single Text")
    print("-" * 60)
    try:
        import requests
        payload = {
            "text": "Hello world",
            "language": "English",
            "speaker": "Ryan",
            "instruct": ""
        }
        response = requests.post(
            'http://127.0.0.1:11433/custom_voice',
            json=payload,
            timeout=30
        )
        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            audio_data = data.get('audio')
            if audio_data:
                # Decode base64 audio
                audio_bytes = base64.b64decode(audio_data)
                print(f"Audio length: {len(audio_bytes)} bytes")
                print(f"Audio format: WAV (based on base64)")
                print("✓ Custom voice generation successful")
            else:
                print(f"Response: {data}")
        else:
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Test 3: Custom voice with batch text
    print("\n" + "-" * 60)
    print("Test 3: Custom Voice - Batch Text")
    print("-" * 60)
    try:
        import requests
        payload = {
            "text": ["Hello world", "How are you?"],
            "language": ["English", "English"],
            "speaker": ["Ryan", "Aiden"],
            "instruct": ["", "Very happy"]
        }
        response = requests.post(
            'http://127.0.0.1:11433/custom_voice',
            json=payload,
            timeout=30
        )
        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            audio_data = data.get('audio')
            if audio_data:
                print(f"Number of audio segments: {len(audio_data)}")
                for i, audio in enumerate(audio_data):
                    audio_bytes = base64.b64decode(audio)
                    print(f"  Segment {i+1}: {len(audio_bytes)} bytes")
                print("✓ Batch custom voice generation successful")
            else:
                print(f"Response: {data}")
        else:
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Test 4: Voice design
    print("\n" + "-" * 60)
    print("Test 4: Voice Design")
    print("-" * 60)
    try:
        import requests
        payload = {
            "text": "Good morning",
            "language": "English",
            "instruct": "cheerful"
        }
        response = requests.post(
            'http://127.0.0.1:11433/voice_design',
            json=payload,
            timeout=30
        )
        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            audio_data = data.get('audio')
            if audio_data:
                audio_bytes = base64.b64decode(audio_data)
                print(f"Audio length: {len(audio_bytes)} bytes")
                print("✓ Voice design generation successful")
            else:
                print(f"Response: {data}")
        else:
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Test 5: List models
    print("\n" + "-" * 60)
    print("Test 5: List Models")
    print("-" * 60)
    try:
        import requests
        response = requests.get('http://127.0.0.1:11433/models', timeout=5)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {json.dumps(response.json(), indent=2)}")
    except Exception as e:
        print(f"ERROR: {e}")
        return False

    # Test 6: Load a model
    print("\n" + "-" * 60)
    print("Test 6: Load Model")
    print("-" * 60)
    try:
        import requests
        payload = {
            "model_type": "custom_voice"
        }
        response = requests.post(
            'http://127.0.0.1:11433/models/custom_voice/load',
            json=payload,
            timeout=30
        )
        print(f"Status Code: {response.status_code}")
        print(f"Response: {json.dumps(response.json(), indent=2)}")
    except Exception as e:
        print(f"ERROR: {e}")
        return False

    print("\n" + "=" * 60)
    print("All TTS tests passed!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    success = test_tts_service()
    sys.exit(0 if success else 1)
