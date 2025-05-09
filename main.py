import os
import uvicorn
import uuid
import httpx
import sys
import json 
import time 
import asyncio
from dotenv import load_dotenv
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from masumi.config import Config
from masumi.payment import Payment 

from logging_config import setup_logging
logger = setup_logging()


# Load environment variables from .env file
load_dotenv(override=True)

# Masumi Configuration
PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL")
PAYMENT_API_KEY = os.getenv("PAYMENT_API_KEY") 
AGENT_IDENTIFIER = os.getenv("AGENT_IDENTIFIER")
SELLER_VKEY = os.getenv("SELLER_VKEY")
NETWORK = os.getenv("NETWORK")

# Price Configuration
PAYMENT_AMOUNT = int(os.getenv("PAYMENT_AMOUNT", "3000000"))
PAYMENT_UNIT = os.getenv("PAYMENT_UNIT", "lovelace")

if not all([PAYMENT_SERVICE_URL, AGENT_IDENTIFIER, SELLER_VKEY, NETWORK]):
    logger.critical("Masumi core environment variables are not fully configured.")
    sys.exit("Error: Core Masumi configuration missing.")

# Kodosumi Configuration
KODOSUMI_BASE_URL = os.getenv("KODOSUMI_BASE_URL")
KODOSUMI_USERNAME = os.getenv("KODOSUMI_USERNAME")
KODOSUMI_PASSWORD = os.getenv("KODOSUMI_PASSWORD")
KODOSUMI_FLOW_NAME_CONTAINS = os.getenv("KODOSUMI_FLOW_NAME_CONTAINS")
KODOSUMI_PAYLOAD_INPUT_KEY = os.getenv("KODOSUMI_PAYLOAD_INPUT_KEY")
KODOSUMI_PRIMARY_FIELD_ID_FOR_PAYLOAD = os.getenv("KODOSUMI_PRIMARY_FIELD_ID_FOR_PAYLOAD")

# Kodosumi Polling Configuration
KODOSUMI_POLL_INTERVAL_SECONDS = int(os.getenv("KODOSUMI_POLL_INTERVAL_SECONDS", "10"))
KODOSUMI_POLL_TIMEOUT_SECONDS = int(os.getenv("KODOSUMI_POLL_TIMEOUT_SECONDS", "300")) 
_terminal_success_statuses_str = os.getenv("KODOSUMI_TERMINAL_SUCCESS_STATUSES", "finished,completed")
KODOSUMI_TERMINAL_SUCCESS_STATUSES = [s.strip().lower() for s in _terminal_success_statuses_str.split(',')]
_terminal_error_statuses_str = os.getenv("KODOSUMI_TERMINAL_ERROR_STATUSES", "failed,error,cancelled,timeout")
KODOSUMI_TERMINAL_ERROR_STATUSES = [s.strip().lower() for s in _terminal_error_statuses_str.split(',')]


if not all([KODOSUMI_BASE_URL, KODOSUMI_USERNAME, KODOSUMI_PASSWORD, KODOSUMI_FLOW_NAME_CONTAINS, KODOSUMI_PAYLOAD_INPUT_KEY, KODOSUMI_PRIMARY_FIELD_ID_FOR_PAYLOAD]):
    logger.critical("Kodosumi environment variables are not fully configured.")
    sys.exit("Error: Kodosumi configuration missing or incomplete.")

# Hardcoded Input Schema Definition
HARDCODED_KODOSUMI_INPUT_FIELDS: List[Dict[str, Any]] = [
    {
        "id": "topic", "type": "string", "name": "Main Topic for Kodosumi Research", "is_required": True, 
        "data": {"description": "The primary topic...", "placeholder": "e.g., AI impact"}
    }
]

if not any(field["id"] == KODOSUMI_PRIMARY_FIELD_ID_FOR_PAYLOAD for field in HARDCODED_KODOSUMI_INPUT_FIELDS):
    logger.critical(f"Config Error: KODOSUMI_PRIMARY_FIELD_ID_FOR_PAYLOAD ('{KODOSUMI_PRIMARY_FIELD_ID_FOR_PAYLOAD}') not in HARDCODED_KODOSUMI_INPUT_FIELDS.")
    sys.exit("Error: Kodosumi primary field ID mismatch.")

logger.info(f"Kodosumi-Masumi Wrapper initializing. Primary Kodosumi input: '{KODOSUMI_PRIMARY_FIELD_ID_FOR_PAYLOAD}', sent as key: '{KODOSUMI_PAYLOAD_INPUT_KEY}'. Polling interval: {KODOSUMI_POLL_INTERVAL_SECONDS}s, Timeout: {KODOSUMI_POLL_TIMEOUT_SECONDS}s.")

app = FastAPI(
    title="Kodosumi-Masumi API Wrapper (MIP-003 Aligned)",
    description="API for running Kodosumi flow tasks with Masumi payment integration, adhering to MIP-003.",
    version="1.0.0"
)

jobs: Dict[str, Dict[str, Any]] = {}
payment_instances: Dict[str, Payment] = {}

try:
    masumi_config = Config(payment_service_url=PAYMENT_SERVICE_URL, payment_api_key=PAYMENT_API_KEY)
except Exception as e:
    logger.critical(f"Failed to initialize Masumi Config: {str(e)}", exc_info=True)
    sys.exit("Error: Could not initialize Masumi Config.")

class StartJobRequest(BaseModel):
    identifier_from_purchaser: str
    input_data: Dict[str, Any]
    class Config:
        json_schema_extra = {"example": {"identifier_from_purchaser": "user_abc_124356", "input_data": {
            f["id"]: f.get("data", {}).get("placeholder", True if f["type"] == "boolean" else (f.get("data",{}).get("values",[None])[0] if f["type"] == "option" else "sample"))
            for f in HARDCODED_KODOSUMI_INPUT_FIELDS}}}

kodosumi_http_client: Optional[httpx.AsyncClient] = None
@app.on_event("startup")
async def startup_event(): global kodosumi_http_client; kodosumi_http_client = httpx.AsyncClient(timeout=60.0, follow_redirects=False) 
@app.on_event("shutdown")
async def shutdown_event(): 
    if kodosumi_http_client: await kodosumi_http_client.aclose()

class KodosumiError(Exception): pass

async def execute_kodosumi_flow_task(job_input_data: Dict[str, Any]) -> Dict[str, Any]:
    if not kodosumi_http_client: raise KodosumiError("Kodosumi HTTP client not initialized.")
    primary_input_value = job_input_data.get(KODOSUMI_PRIMARY_FIELD_ID_FOR_PAYLOAD)
    payload_val = str(primary_input_value) if not isinstance(primary_input_value, (str, int, float, bool)) else primary_input_value
    if primary_input_value is None and any(f["id"] == KODOSUMI_PRIMARY_FIELD_ID_FOR_PAYLOAD and f.get("is_required") for f in HARDCODED_KODOSUMI_INPUT_FIELDS):
        raise KodosumiError(f"Missing required primary input field '{KODOSUMI_PRIMARY_FIELD_ID_FOR_PAYLOAD}' for Kodosumi.")
    logger.info(f"Executing Kodosumi task. Field '{KODOSUMI_PRIMARY_FIELD_ID_FOR_PAYLOAD}' (value: '{str(payload_val)[:50]}...') as payload key '{KODOSUMI_PAYLOAD_INPUT_KEY}'.")
    
    kodosumi_job_status_url = None
    try:
        # 1. Login
        login_params = {"name": KODOSUMI_USERNAME, "password": KODOSUMI_PASSWORD}
        resp_login = await kodosumi_http_client.get(f"{KODOSUMI_BASE_URL}/login", params=login_params)
        resp_login.raise_for_status(); token1 = resp_login.json().get("KODOSUMI_API_KEY")
        if not token1: raise KodosumiError("Auth failed: KODOSUMI_API_KEY missing.")
        headers = {"kodosumi_api_key": token1}

        # 2. Find Flow
        resp_flows = await kodosumi_http_client.get(f"{KODOSUMI_BASE_URL}/flow", headers=headers)
        resp_flows.raise_for_status(); flows_data = resp_flows.json()
        target_flow = next((f for f in flows_data.get("items", []) if isinstance(f, dict) and KODOSUMI_FLOW_NAME_CONTAINS.lower() in f.get("summary", "").lower()), None)
        if not target_flow or not target_flow.get("url"): raise KodosumiError(f"Target Kodosumi flow '{KODOSUMI_FLOW_NAME_CONTAINS}' not found.")
        
        # 3. Trigger Flow (POST)
        kodosumi_payload = {KODOSUMI_PAYLOAD_INPUT_KEY: payload_val}
        trigger_headers = {**headers, "Accept": "text/plain"}
        resp_trigger = await kodosumi_http_client.post(f"{KODOSUMI_BASE_URL}{target_flow['url']}", headers=trigger_headers, data=kodosumi_payload)

        if resp_trigger.status_code >= 400:
            logger.error(f"Kodosumi flow trigger POST failed with status {resp_trigger.status_code}: {resp_trigger.text[:200]}")
            resp_trigger.raise_for_status() 
        
        if resp_trigger.is_redirect or resp_trigger.status_code == 302:
            kodosumi_job_status_url = resp_trigger.headers.get("location")
            if not kodosumi_job_status_url:
                raise KodosumiError(f"Kodosumi flow trigger returned redirect status {resp_trigger.status_code} but no 'Location' header.")
            logger.info(f"Kodosumi flow trigger POST successful (status {resp_trigger.status_code}), Kodosumi job status URL: {kodosumi_job_status_url}")
        elif 200 <= resp_trigger.status_code < 300 and "application/json" in resp_trigger.headers.get("content-type", "").lower():
            logger.info(f"Kodosumi flow trigger POST successful (status {resp_trigger.status_code}) with direct JSON response (job might be very fast).")
            direct_json_result = resp_trigger.json()
            kodosumi_status_val = direct_json_result.get("status") 
            kodosumi_status = str(kodosumi_status_val).lower() if kodosumi_status_val is not None else ""

            if kodosumi_status in KODOSUMI_TERMINAL_SUCCESS_STATUSES:
                return direct_json_result
            elif kodosumi_status in KODOSUMI_TERMINAL_ERROR_STATUSES:
                 raise KodosumiError(f"Kodosumi job failed immediately with status '{kodosumi_status}'. Response: {str(direct_json_result)[:200]}")
            else: 
                 raise KodosumiError(f"Kodosumi flow trigger POST successful (status {resp_trigger.status_code}) but no redirect and non-terminal/unknown status in direct JSON: {kodosumi_status}")
        else:
            raise KodosumiError(f"Kodosumi flow trigger POST returned unexpected status {resp_trigger.status_code} and no redirect/JSON.")

        # 4. Poll Kodosumi Job Status URL
        start_time = time.time()
        while True:
            if time.time() - start_time > KODOSUMI_POLL_TIMEOUT_SECONDS:
                raise KodosumiError(f"Kodosumi job polling timed out after {KODOSUMI_POLL_TIMEOUT_SECONDS} seconds for URL: {kodosumi_job_status_url}")

            logger.info(f"Polling Kodosumi job status at: {KODOSUMI_BASE_URL}{kodosumi_job_status_url}")
            resp_status = await kodosumi_http_client.get(f"{KODOSUMI_BASE_URL}{kodosumi_job_status_url}", headers=headers)
            resp_status.raise_for_status() 
            
            status_json = resp_status.json()
            kodosumi_status_value = status_json.get("status")
            current_kodosumi_status = str(kodosumi_status_value).lower() if kodosumi_status_value is not None else ""
            
            logger.info(f"Kodosumi job status: '{current_kodosumi_status}'. Full response keys: {list(status_json.keys())}")

            if current_kodosumi_status in KODOSUMI_TERMINAL_SUCCESS_STATUSES:
                logger.info(f"Kodosumi job finished successfully with status '{current_kodosumi_status}'.")
                return status_json 
            elif current_kodosumi_status in KODOSUMI_TERMINAL_ERROR_STATUSES:
                error_detail = status_json.get("error", "No error details provided by Kodosumi.")
                logger.error(f"Kodosumi job failed with status '{current_kodosumi_status}'. Details: {error_detail}")
                raise KodosumiError(f"Kodosumi job failed with status '{current_kodosumi_status}'. Details: {str(error_detail)[:200]}")
            
            logger.info(f"Kodosumi job status is '{current_kodosumi_status}', not terminal. Waiting {KODOSUMI_POLL_INTERVAL_SECONDS}s before next poll.")
            await asyncio.sleep(KODOSUMI_POLL_INTERVAL_SECONDS)

    except httpx.HTTPStatusError as e: 
        raise KodosumiError(f"Kodosumi API HTTP error: {e.response.status_code} - {e.response.text[:200]}")
    except httpx.RequestError as e: raise KodosumiError(f"Kodosumi connection error: {str(e)}")
    except Exception as e: raise KodosumiError(f"Unexpected Kodosumi error: {str(e)}")

@app.post("/start_job")
async def start_job(data: StartJobRequest):
    job_id = str(uuid.uuid4())
    logger.info(f"Job {job_id}: /start_job from '{data.identifier_from_purchaser}'. Validating inputs: {list(data.input_data.keys())}")
    for field_def in HARDCODED_KODOSUMI_INPUT_FIELDS:
        fid, val = field_def["id"], data.input_data.get(field_def["id"])
        if field_def.get("is_required") and fid not in data.input_data:
            raise HTTPException(status_code=400, detail=f"Missing required field: '{fid}'.")
        if fid in data.input_data: 
            etype = field_def["type"]
            mismatch = (etype == "string" and not isinstance(val, str)) or \
                       (etype == "number" and not isinstance(val, (int, float))) or \
                       (etype == "boolean" and not isinstance(val, bool)) or \
                       (etype == "option" and (not isinstance(val, str) or ("values" in field_def.get("data",{}) and val not in field_def["data"]["values"])))
            if mismatch: raise HTTPException(status_code=400, detail=f"Type/value error for '{fid}'. Expected {etype} compatible. Got {type(val).__name__} or invalid option.")
            # TODO: Implement detailed field_def["validations"] (min, max, format)

    try:
        import hashlib
        input_json = json.dumps(data.input_data, sort_keys=True, separators=(',', ':'))
        input_hash = hashlib.sha256(input_json.encode('utf-8')).hexdigest()
        logger.info(f"Job {job_id}: Calculated input_hash: {input_hash}")

        payment = Payment(agent_identifier=AGENT_IDENTIFIER, config=masumi_config, 
                          identifier_from_purchaser=data.identifier_from_purchaser,
                          input_data=data.input_data, network=NETWORK) 
        
        payment_req_data = await payment.create_payment_request()
        if not payment_req_data or payment_req_data.get("status") != "success" or "data" not in payment_req_data:
            raise HTTPException(status_code=500, detail=f"Masumi payment request failed: {str(payment_req_data)[:200]}")

        masumi_details = payment_req_data["data"]
        payment_id = masumi_details["blockchainIdentifier"] 
        logger.info(f"Job {job_id}: Masumi payment request created. Payment ID: {payment_id}")

        jobs[job_id] = {"status": "awaiting_payment", "payment_status": "pending", "masumi_payment_id": payment_id, 
                        "input_data": data.input_data, "result": None, "error": None, 
                        "message": "Awaiting payment confirmation.", "identifier_from_purchaser": data.identifier_from_purchaser,
                        "input_hash_calculated": input_hash}

        async def masumi_library_event_callback(event_payment_id_from_masumi: str):
            logger.info(f"Job {job_id}: Masumi callback received. Event for Masumi payment_id: '{event_payment_id_from_masumi}'. This job expects: '{payment_id}'.")
            if event_payment_id_from_masumi == payment_id:
                logger.info(f"Job {job_id}: Matched event_payment_id '{event_payment_id_from_masumi}'. Proceeding to handle payment confirmation.")
                current_job_state = jobs.get(job_id)
                if current_job_state and current_job_state["status"] == "awaiting_payment":
                     await handle_payment_confirmation(job_id, payment_id) 
                elif current_job_state:
                     logger.info(f"Job {job_id}: Payment event for '{event_payment_id_from_masumi}' received, but job status is '{current_job_state['status']}'. No action by callback.")
            else:
                logger.warning(f"Job {job_id}: Masumi callback for payment_id '{event_payment_id_from_masumi}' does NOT match this job's payment_id '{payment_id}'. Ignoring event.")

        payment_instances[job_id] = payment
        await payment.start_status_monitoring(masumi_library_event_callback)
        logger.info(f"Job {job_id}: Masumi payment status monitoring started for payment ID {payment_id}.")
        
        final_input_hash = getattr(payment, 'input_hash', input_hash)
        if final_input_hash == input_hash and not hasattr(payment, 'input_hash'):
            logger.info(f"Job {job_id}: Using self-calculated input_hash.")

        response_payload = {
            "status": "success", 
            "job_id": job_id, 
            "blockchainIdentifier": payment_id,
            "submitResultTime": masumi_details.get("submitResultTime"), 
            "unlockTime": masumi_details.get("unlockTime"),
            "externalDisputeUnlockTime": masumi_details.get("externalDisputeUnlockTime"),
            "agentIdentifier": AGENT_IDENTIFIER, 
            "sellerVkey": SELLER_VKEY,
            "identifierFromPurchaser": data.identifier_from_purchaser,
            "input_hash": final_input_hash,
            "amounts": [
                {
                    "amount": PAYMENT_AMOUNT,
                    "unit": PAYMENT_UNIT
                }
            ]
        }
        return response_payload
    except HTTPException: raise
    except Exception as e:
        logger.error(f"Job {job_id}: Error in /start_job for '{data.identifier_from_purchaser}': {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

async def handle_payment_confirmation(job_id: str, masumi_payment_id: str):
    job_info = jobs.get(job_id)
    if not job_info or job_info.get("status") != "awaiting_payment":
        logger.warning(f"Job {job_id}: handle_payment_confirmation for '{masumi_payment_id}' skipped or already processed. Status: {job_info.get('status') if job_info else 'N/A'}.")
        return
    logger.info(f"Job {job_id}: Payment '{masumi_payment_id}' confirmed. Processing Kodosumi task.")
    job_info.update({"status": "running", "payment_status": "confirmed", "processed_payment_id": masumi_payment_id, "message": "Payment confirmed, Kodosumi task is now running (polling for completion)."})
    try:
        kodosumi_result_obj = await execute_kodosumi_flow_task(job_info["input_data"]) 
        logger.info(f"Job {job_id}: Kodosumi task completed and result object received.")
        
        # Store the full Kodosumi result object internally for now
        job_info["kodosumi_full_result"] = kodosumi_result_obj 

        if job_id in payment_instances:
            try: await payment_instances[job_id].complete_payment(masumi_payment_id, kodosumi_result_obj) # Pass full obj to Masumi
            except Exception as e_mc: logger.error(f"Job {job_id}: Failed marking Masumi payment {masumi_payment_id} complete: {e_mc}", exc_info=True)
        
        job_info.update({"status": "completed", "payment_status": "completed", 
                         # "result" will be formatted by /status endpoint
                         "error": None, "message": "Task completed successfully."})
    except KodosumiError as e_k:
        logger.error(f"Job {job_id}: Kodosumi task processing failed: {str(e_k)}") 
        job_info.update({"status": "failed", "error": f"Kodosumi error: {str(e_k)}", "payment_status": "confirmed_kodosumi_failed", "message": f"Task failed: {str(e_k)}"})
    except Exception as e_g: 
        logger.error(f"Job {job_id}: Unexpected error during Kodosumi task execution or Masumi completion: {str(e_g)}", exc_info=True)
        job_info.update({"status": "failed", "error": f"Task execution error: {str(e_g)}", "message": "Task failed unexpectedly."})
    finally:
        if job_id in payment_instances and job_info.get("status") in ["completed", "failed", "payment_failed"]:
            logger.info(f"Job {job_id}: Stopping Masumi monitoring. Final status: {job_info['status']}.")
            try: payment_instances[job_id].stop_status_monitoring()
            except Exception as e_sm: logger.error(f"Job {job_id}: Error stopping Masumi monitoring: {e_sm}", exc_info=True)

@app.get("/status")
async def get_status_endpoint(job_id: str = Query(..., description="Job ID to check.")):
    logger.info(f"Received /status request for job_id: {job_id}")
    if job_id not in jobs: 
        logger.warning(f"Job {job_id} not found for /status request.")
        raise HTTPException(status_code=404, detail="Job not found")
    
    jd = jobs[job_id]
    
    response_payload = {
        "job_id": job_id,
        "status": jd["status"],
        "message": jd.get("message") 
    }

    # Add "result" field if job is completed and result is available
    if jd["status"] == "completed" and "kodosumi_full_result" in jd:
        kodosumi_full_result = jd.get("kodosumi_full_result")
        extracted_string_result = None
        try:
            # Attempt to extract the raw string as per user's example
            extracted_string_result = kodosumi_full_result.get("final", {}).get("CrewOutput", {}).get("raw")
            if not isinstance(extracted_string_result, str):
                logger.warning(f"Job {job_id}: Extracted 'raw' result is not a string, it's {type(extracted_string_result)}. Full Kodosumi result: {str(kodosumi_full_result)[:500]}")
                # Fallback or decide how to handle if not a string. For now, setting to None if not string.
                if extracted_string_result is not None: # If it's not None but also not a string
                     extracted_string_result = str(extracted_string_result) # Convert to string as a fallback
                else: # If it's None
                    extracted_string_result = None # Keep it None
        except Exception as e:
            logger.error(f"Job {job_id}: Error extracting 'raw' string from Kodosumi result: {str(e)}. Full Kodosumi result: {str(kodosumi_full_result)[:500]}", exc_info=True)
            extracted_string_result = None # Or an error message string
        
        response_payload["result"] = extracted_string_result
    elif "error" in jd and jd["error"]: # If job failed, message might contain error. Result is typically null.
        response_payload["result"] = None


    # The "input_data" field is optional and only required if status is "awaiting_input"
    # This service does not currently use "awaiting_input" status for Kodosumi flow.
    # If it did, it would look like:
    # if jd["status"] == "awaiting_input":
    # response_payload["input_data"] = [ ... schema for awaited input ... ]

    logger.debug(f"Returning status for job {job_id}: { {k: (str(v)[:100] + '...' if isinstance(v, str) and len(v) > 100 else v) for k,v in response_payload.items()} }") # Log truncated result
    return response_payload

# Uncomment if you want to use the /provide_input endpoint.
#@app.post("/provide_input")
#async def provide_input(job_id: str, input_data: Dict[str, Any]):
#    if job_id not in jobs: raise HTTPException(status_code=404, detail="Job not found")
#    logger.warning(f"/provide_input is stub for job {job_id}.")
#    return {"status": "success", "message": "Input received (stub endpoint)."}

@app.get("/availability")
async def check_availability():
    #return {"status": "available", "agentidentifier": AGENT_IDENTIFIER, "message": "Server operational."}
    return {"type": "masumi-agent"}
@app.get("/input_schema")
async def input_schema():
    logger.info("Received /input_schema request.")
    output_fields = []
    for field in HARDCODED_KODOSUMI_INPUT_FIELDS:
        transformed = {"id": field["id"], "type": field["type"]}
        if "name" in field: transformed["name"] = field["name"]
        if "data" in field and field["data"]: transformed["data"] = field["data"]
        
        validations = [v for v in field.get("validations", []) if v.get("validation") not in ["required", "optional"]]
        
        if validations: transformed["validations"] = validations
        output_fields.append(transformed)
    return {"input_data": output_fields}

@app.get("/health")
async def health(): return {"status": "healthy", "message": "Service operational."}

if __name__ == "__main__":
    if not all([PAYMENT_SERVICE_URL, AGENT_IDENTIFIER, SELLER_VKEY, NETWORK, KODOSUMI_BASE_URL, KODOSUMI_USERNAME, 
                KODOSUMI_PASSWORD, KODOSUMI_FLOW_NAME_CONTAINS, KODOSUMI_PAYLOAD_INPUT_KEY, KODOSUMI_PRIMARY_FIELD_ID_FOR_PAYLOAD]):
        logger.critical("CRITICAL: Essential environment variables missing. Server cannot start.")
        sys.exit(1)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level=os.getenv("LOG_LEVEL", "info").lower())