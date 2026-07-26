// Tauri backend for Hermes Voice Frontend
// Bridges the frontend to the Hermes bridge server (port 8420)

use serde::{Deserialize, Serialize};
use std::sync::Mutex;
use tauri::{command, State};
use base64::{Engine as _, engine::general_purpose};

/// Session state stored per-window
struct SessionState {
    session_id: Mutex<Option<String>>,
}

/// Request/response types matching the bridge server API
#[derive(Serialize, Deserialize)]
struct ChatRequest {
    message: String,
    session_id: Option<String>,
    voice: Option<bool>,
}

#[derive(Serialize, Deserialize)]
struct ChatResponse {
    text: String,
    session_id: String,
    audio_url: Option<String>,
    user_text: Option<String>,
}

#[derive(Serialize, Deserialize)]
struct HealthResponse {
    status: String,
    stt: bool,
    tts: bool,
}

/// Get the bridge server base URL
fn bridge_url() -> String {
    std::env::var("HERMES_BRIDGE_URL").unwrap_or_else(|_| "http://127.0.0.1:8420".to_string())
}

/// Tauri command: send a text message to Hermes
#[command]
async fn send_message(
    state: State<'_, SessionState>,
    message: String,
    voice: Option<bool>,
) -> Result<ChatResponse, String> {
    let client = reqwest::Client::new();
    let url = format!("{}/api/chat", bridge_url());

    // Get current session_id
    let session_id = state.session_id.lock().unwrap().clone();

    let request = ChatRequest {
        message,
        session_id,
        voice,
    };

    let response = client
        .post(&url)
        .json(&request)
        .send()
        .await
        .map_err(|e| format!("Request failed: {}", e))?;

    if !response.status().is_success() {
        let err = response.text().await.unwrap_or_default();
        return Err(format!("API error: {}", err));
    }

    let mut chat_response: ChatResponse = response
        .json()
        .await
        .map_err(|e| format!("Parse error: {}", e))?;

    // Store session_id for next request
    if !chat_response.session_id.is_empty() {
        *state.session_id.lock().unwrap() = Some(chat_response.session_id.clone());
    }

    // Convert relative audio_url to absolute
    if let Some(audio_url) = &chat_response.audio_url {
        if audio_url.starts_with("/api/audio/") {
            chat_response.audio_url = Some(format!("{}{}", bridge_url(), audio_url));
        }
    }

    Ok(chat_response)
}

/// Tauri command: send audio blob for STT
#[command]
async fn send_audio(
    state: State<'_, SessionState>,
    audio_base64: String,
    mime_type: String,
) -> Result<ChatResponse, String> {
    let client = reqwest::Client::new();
    let url = format!("{}/api/chat", bridge_url());

    let session_id = state.session_id.lock().unwrap().clone();

    // Decode base64 audio
    let audio_bytes = general_purpose::STANDARD.decode(&audio_base64)
        .map_err(|e| format!("Base64 decode error: {}", e))?;

    // Build multipart form
    let part = reqwest::multipart::Part::bytes(audio_bytes)
        .file_name("input.webm")
        .mime_str(&mime_type)
        .map_err(|e| format!("Mime error: {}", e))?;

    let mut form = reqwest::multipart::Form::new().part("audio", part);
    if let Some(sid) = session_id {
        form = form.text("session_id", sid);
    }

    let response = client
        .post(&url)
        .multipart(form)
        .send()
        .await
        .map_err(|e| format!("Request failed: {}", e))?;

    if !response.status().is_success() {
        let err = response.text().await.unwrap_or_default();
        return Err(format!("API error: {}", err));
    }

    let mut chat_response: ChatResponse = response
        .json()
        .await
        .map_err(|e| format!("Parse error: {}", e))?;

    if !chat_response.session_id.is_empty() {
        *state.session_id.lock().unwrap() = Some(chat_response.session_id.clone());
    }

    if let Some(audio_url) = &chat_response.audio_url {
        if audio_url.starts_with("/api/audio/") {
            chat_response.audio_url = Some(format!("{}{}", bridge_url(), audio_url));
        }
    }

    Ok(chat_response)
}

/// Tauri command: check bridge server health
#[command]
async fn check_health() -> Result<HealthResponse, String> {
    let client = reqwest::Client::new();
    let url = format!("{}/api/health", bridge_url());

    let response = client
        .get(&url)
        .send()
        .await
        .map_err(|e| format!("Health check failed: {}", e))?;

    if !response.status().is_success() {
        return Err("Health check returned non-200".into());
    }

    response
        .json()
        .await
        .map_err(|e| format!("Parse error: {}", e))
}

/// Tauri command: clear session (start new conversation)
#[command]
fn clear_session(state: State<'_, SessionState>) {
    *state.session_id.lock().unwrap() = None;
}

/// Tauri command: get current session_id
#[command]
fn get_session_id(state: State<'_, SessionState>) -> Option<String> {
    state.session_id.lock().unwrap().clone()
}

fn main() {
    tauri::Builder::default()
        .manage(SessionState {
            session_id: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![
            send_message,
            send_audio,
            check_health,
            clear_session,
            get_session_id
        ])
        .run(tauri::generate_context!("tauri.conf.json"))
        .expect("error while running tauri application");
}