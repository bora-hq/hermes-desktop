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

/// Design System types
#[derive(Serialize, Deserialize)]
struct DesignScanRequest {
    workspace: String,
    output: Option<String>,
}

#[derive(Serialize, Deserialize)]
struct DesignScanResponse {
    ok: bool,
    tokens: usize,
    components: usize,
    output: String,
    metadata: serde_json::Value,
}

#[derive(Serialize, Deserialize)]
struct DesignTokensResponse {
    tokens: serde_json::Value,
    count: usize,
}

#[derive(Serialize, Deserialize)]
struct DesignExportRequest {
    html: String,
    tokens: serde_json::Value,
    components: serde_json::Value,
    title: Option<String>,
    framework: Option<String>,
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

/// Tauri command: scan workspace for design tokens
#[command]
async fn scan_design_system(workspace: Option<String>) -> Result<DesignScanResponse, String> {
    let client = reqwest::Client::new();
    let url = format!("{}/api/design-system/scan", bridge_url());

    let request = DesignScanRequest {
        workspace: workspace.unwrap_or_else(|| ".".to_string()),
        output: Some("design-system.json".to_string()),
    };

    let response = client
        .post(&url)
        .json(&request)
        .send()
        .await
        .map_err(|e| format!("Scan request failed: {}", e))?;

    if !response.status().is_success() {
        let err = response.text().await.unwrap_or_default();
        return Err(format!("API error: {}", err));
    }

    response
        .json()
        .await
        .map_err(|e| format!("Parse error: {}", e))
}

/// Tauri command: get design tokens
#[command]
async fn get_design_tokens(token_type: Option<String>) -> Result<DesignTokensResponse, String> {
    let client = reqwest::Client::new();
    let url = if let Some(t) = token_type {
        format!("{}/api/design-system/tokens?type={}", bridge_url(), t)
    } else {
        format!("{}/api/design-system/tokens", bridge_url())
    };

    let response = client
        .get(&url)
        .send()
        .await
        .map_err(|e| format!("Request failed: {}", e))?;

    if !response.status().is_success() {
        let err = response.text().await.unwrap_or_default();
        return Err(format!("API error: {}", err));
    }

    response
        .json()
        .await
        .map_err(|e| format!("Parse error: {}", e))
}

/// Tauri command: get theme CSS variables
#[command]
async fn get_theme_css() -> Result<String, String> {
    let client = reqwest::Client::new();
    let url = format!("{}/api/design-system/theme.css", bridge_url());

    let response = client
        .get(&url)
        .send()
        .await
        .map_err(|e| format!("Request failed: {}", e))?;

    if !response.status().is_success() {
        let err = response.text().await.unwrap_or_default();
        return Err(format!("API error: {}", err));
    }

    response
        .text()
        .await
        .map_err(|e| format!("Parse error: {}", e))
}

/// Tauri command: get Tailwind config
#[command]
async fn get_tailwind_config() -> Result<String, String> {
    let client = reqwest::Client::new();
    let url = format!("{}/api/design-system/tailwind.config.js", bridge_url());

    let response = client
        .get(&url)
        .send()
        .await
        .map_err(|e| format!("Request failed: {}", e))?;

    if !response.status().is_success() {
        let err = response.text().await.unwrap_or_default();
        return Err(format!("API error: {}", err));
    }

    response
        .text()
        .await
        .map_err(|e| format!("Parse error: {}", e))
}

/// Tauri command: export as HTML bundle
#[command]
async fn export_html(
    html: String,
    tokens: serde_json::Value,
    components: serde_json::Value,
    title: Option<String>,
) -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    let url = format!("{}/api/design/export/html", bridge_url());

    let request = DesignExportRequest {
        html,
        tokens,
        components,
        title,
        framework: None,
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

    response
        .json()
        .await
        .map_err(|e| format!("Parse error: {}", e))
}

/// Tauri command: export as code bundle (React/Vue/Svelte)
#[command]
async fn export_code_bundle(
    html: String,
    tokens: serde_json::Value,
    components: serde_json::Value,
    framework: String,
) -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    let url = format!("{}/api/design/export/code-bundle", bridge_url());

    let request = DesignExportRequest {
        html,
        tokens,
        components,
        title: None,
        framework: Some(framework),
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

    response
        .json()
        .await
        .map_err(|e| format!("Parse error: {}", e))
}

/// Tauri command: export as PPTX
#[command]
async fn export_pptx(
    html: String,
    tokens: serde_json::Value,
    title: Option<String>,
) -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    let url = format!("{}/api/design/export/pptx", bridge_url());

    let request = DesignExportRequest {
        html,
        tokens,
        components: serde_json::Value::Object(serde_json::Map::new()),
        title,
        framework: None,
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

    response
        .json()
        .await
        .map_err(|e| format!("Parse error: {}", e))
}

/// Tauri command: export as Figma plugin
#[command]
async fn export_figma(
    html: String,
    tokens: serde_json::Value,
    components: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    let url = format!("{}/api/design/export/figma", bridge_url());

    let request = DesignExportRequest {
        html,
        tokens,
        components,
        title: None,
        framework: None,
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

    response
        .json()
        .await
        .map_err(|e| format!("Parse error: {}", e))
}

/// Tauri command: get Tailwind config for design system
#[command]
async fn get_design_tailwind() -> Result<String, String> {
    let client = reqwest::Client::new();
    let url = format!("{}/api/design-system/tailwind.config.js", bridge_url());

    let response = client
        .get(&url)
        .send()
        .await
        .map_err(|e| format!("Request failed: {}", e))?;

    if !response.status().is_success() {
        let err = response.text().await.unwrap_or_default();
        return Err(format!("API error: {}", err));
    }

    response
        .text()
        .await
        .map_err(|e| format!("Parse error: {}", e))
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
            get_session_id,
            // Design System commands
            scan_design_system,
            get_design_tokens,
            get_theme_css,
            get_tailwind_config,
            // Export commands
            export_html,
            export_code_bundle,
            export_pptx,
            export_figma,
            get_design_tailwind,
        ])
        .run(tauri::generate_context!("tauri.conf.json"))
        .expect("error while running tauri application");
}