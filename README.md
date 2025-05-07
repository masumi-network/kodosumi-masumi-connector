
# Kodosumi-Masumi API Wrapper (MIP-003 Compliant)

## Overview

This FastAPI application serves as a bridge between the Masumi payment network and a Kodosumi AI flow execution service. It exposes an API compliant with the MIP-003 standard, allowing users to:

- Discover the service's input requirements (`/input_schema`)
- Initiate a job with specific inputs (`/start_job`)
- Receive Masumi payment details to complete the payment
- Automatically trigger a designated Kodosumi flow upon successful Masumi payment confirmation
- Poll the Kodosumi flow for completion
- Check job status and retrieve the final result (`/status`)

The wrapper handles:
- Authentication with Kodosumi
- Dynamic flow discovery and triggering
- Polling for completion
- Integration with the Masumi payment library

---

## Prerequisites

- **Python**: Version 3.8 or higher
- **pip**: Python package installer
- **Virtual Environment Tool**: `venv` (recommended)
- **Masumi Python Library**: Must be installable, includes `masumi.config.Config` and `masumi.payment.Payment`
- **Kodosumi Instance Access**: Valid base URL, username, and password
- **Logging Configuration**: `logging_config.py` file with `setup_logging()` function

---

## Setup Steps

### 1. Get the Code

- Save the Python script as `kodosumi_masumi_wrapper.py`
- Ensure `logging_config.py` is in the same directory
- Create a `requirements.txt` file (as provided previously)

### 2. Create and Activate Virtual Environment

```bash
python3 -m venv .venv
```

Activate the environment:

- **macOS/Linux**:  
  ```bash
  source .venv/bin/activate
  ```

- **Windows (CMD)**:  
  ```cmd
  .\.venv\Scripts\activate.bat
  ```

- **Windows (PowerShell)**:  
  ```powershell
  .\.venv\Scripts\Activate.ps1
  ```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

Install the Masumi library (custom install if needed):

```bash
pip install -e /path/to/local/masumi
# or
pip install git+https://...
```

### 4. Configure Environment Variables (`.env` file)

Create a `.env` file in the project directory:

```dotenv
# Masumi Configuration
PAYMENT_SERVICE_URL="http://your_masumi_payment_service_url"
PAYMENT_API_KEY="your_masumi_payment_api_key"
AGENT_IDENTIFIER="your_unique_agent_identifier_registered_with_masumi"
SELLER_VKEY="your_masumi_seller_verification_key"
NETWORK="Cardano Mainnet"

# Kodosumi Configuration
KODOSUMI_BASE_URL="http://your_kodosumi_instance_url"
KODOSUMI_USERNAME="your_kodosumi_username"
KODOSUMI_PASSWORD="your_kodosumi_password"
KODOSUMI_FLOW_NAME_CONTAINS="hymn"

# Input Mapping
KODOSUMI_PAYLOAD_INPUT_KEY="topic"
KODOSUMI_PRIMARY_FIELD_ID_FOR_PAYLOAD="topic"

# Polling Configuration
KODOSUMI_POLL_INTERVAL_SECONDS="10"
KODOSUMI_POLL_TIMEOUT_SECONDS="300"
KODOSUMI_TERMINAL_SUCCESS_STATUSES="finished,completed"
KODOSUMI_TERMINAL_ERROR_STATUSES="failed,error,cancelled,timeout"

# Logging
LOG_LEVEL="INFO"
```

> ⚠️ **Security:** Do not commit your `.env` file to version control.

---

## Review Hardcoded Schema (Optional)

- Open `kodosumi_masumi_wrapper.py`
- Review and modify `HARDCODED_KODOSUMI_INPUT_FIELDS` to match your flow’s requirements

---

## Running the Wrapper

Activate your virtual environment and run:

```bash
uvicorn kodosumi_masumi_wrapper:app --host 0.0.0.0 --port 8000 --reload
```

- `--host 0.0.0.0` makes it network-accessible
- `--port 8000` sets the listening port
- `--reload` restarts on code changes (for development)

Visit:

- Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## API Endpoints (MIP-003)

- `GET /availability`: Server status and agent ID
- `GET /input_schema`: Returns input schema
- `POST /start_job`: Validates input, creates payment request
- `GET /status?job_id={job_id}`: Checks job status and result
- `POST /provide_input`: (Stub only) For intermediate inputs
- `GET /health`: Basic health check

---

## Kodosumi Interaction Flow

1. **Payment Confirmation**: Waits for Masumi payment callback
2. **Authentication**: Logs into Kodosumi (`/login`)
3. **Flow Discovery**: Searches `/flow` for target
4. **Trigger Flow**: Sends `POST` with input payload
5. **Get Status URL**: Expects redirect with poll URL
6. **Polling**: Periodically checks job status
7. **Completion/Error**: Resolves when terminal status is reached
8. **Result Return**: Final result sent to `/status`

---

## Important Notes

- **Production Readiness**: Current job storage is in-memory. Use a persistent store for production.
- **Masumi Library**: Ensure compatibility with your version of the Masumi library.
- **Error Handling**: Basic handling is included; expand for robustness.
- **Input Validation**: `/start_job` does simple schema validation. Add more if needed.
