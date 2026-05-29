import os
from core.config import get_model, get_api_key, get_client_type

print("Client Type:", get_client_type())
print("Model:", get_model())
print("API Key loaded:", bool(get_api_key()))
