import os
import uuid
from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from temporalio.client import Client

router = APIRouter()

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatHistoryResponse(BaseModel):
    messages: List[Dict[str, Any]]

# A constant workflow ID for the global swarm room
SWARM_WORKFLOW_ID = "global-swarm-orchestrator"

async def get_temporal_client() -> Client:
    temporal_address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    return await Client.connect(temporal_address, namespace="sailratings")

@router.get("/chat", response_model=ChatHistoryResponse)
async def get_chat_history():
    """Fetches the unified chat history from the Swarm Orchestrator workflow."""
    client = await get_temporal_client()
    try:
        handle = client.get_workflow_handle(SWARM_WORKFLOW_ID)
        history = await handle.query("get_history")
        return {"messages": history}
    except Exception as e:
        # If it doesn't exist, we just return empty so the UI can prompt initialization
        return {"messages": []}

@router.post("/chat")
async def post_message(msg: ChatMessage, background_tasks: BackgroundTasks):
    """Sends a message to the unified swarm chat room."""
    client = await get_temporal_client()
    
    # Check if workflow is running, if not, start it
    try:
        from temporalio.client import WorkflowExecutionStatus
        handle = client.get_workflow_handle(SWARM_WORKFLOW_ID)
        desc = await handle.describe()
        if desc.status != WorkflowExecutionStatus.RUNNING:
            raise ValueError("Workflow is not running")
    except Exception:
        # Workflow doesn't exist or is closed. Start a new one!
        from irc_data.temporal.orchestrator.swarm import SwarmOrchestratorWorkflow
        from temporalio.common import WorkflowIDReusePolicy
        await client.start_workflow(
            SwarmOrchestratorWorkflow.run,
            id=SWARM_WORKFLOW_ID,
            task_queue="orchestrator-task-queue",
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE
        )
        handle = client.get_workflow_handle(SWARM_WORKFLOW_ID)
        
    # Send the signal
    await handle.signal("receive_message", {"role": msg.role, "content": msg.content})
    
    return {"status": "ok", "message": "Signal sent to Swarm Orchestrator"}
