/**
 * Design Canvas - Live Prototyping Engine for Hermes Voice
 * Inspired by Claude Design's live preview with voice commands
 */

class DesignCanvas {
  constructor(options = {}) {
    this.container = options.container || document.body;
    this.bridgeUrl = options.bridgeUrl || 'http://127.0.0.1:8420';
    this.designTokens = options.designTokens || {};
    this.componentRegistry = options.componentRegistry || {};
    
    this.iframe = null;
    this.messageChannel = null;
    this.port = null;
    this.snapshotHistory = [];
    this.currentSnapshotIndex = -1;
    this.isRecording = false;
    
    this.init();
  }
  
  async init() {
    await this.loadDesignSystem();
    this.createCanvas();
    this.setupMessageChannel();
    this.setupVoiceCommands();
    this.setupKeyboardShortcuts();
  }
  
  async loadDesignSystem() {
    try {
      // Load tokens
      const tokensRes = await fetch(`${this.bridgeUrl}/api/design-system/tokens`);
      const tokensData = await tokensRes.json();
      this.designTokens = tokensData.tokens || {};
      
      // Load theme CSS
      const cssRes = await fetch(`${this.bridgeUrl}/api/design-system/theme.css`);
      this.themeCSS = await cssRes.text();
      
      console.log('[DesignCanvas] Design system loaded:', Object.keys(this.designTokens).length, 'tokens');
    } catch (e) {
      console.warn('[DesignCanvas] Could not load design system:', e);
      this.themeCSS = ':root { --hds-bg: #030712; --hds-fg: #f5f5f0; --hds-accent: #ff8c00; }';
    }
  }
  
  createCanvas() {
    // Canvas container
    this.canvasContainer = document.createElement('div');
    this.canvasContainer.className = 'design-canvas-container';
    this.canvasContainer.style.cssText = `
      position: relative;
      width: 100%;
      height: 100%;
      display: flex;
      flex-direction: column;
      background: var(--hds-bg, #030712);
    `;
    
    // Toolbar
    this.toolbar = this.createToolbar();
    this.canvasContainer.appendChild(this.toolbar);
    
    // Device toolbar
    this.deviceToolbar = this.createDeviceToolbar();
    this.canvasContainer.appendChild(this.deviceToolbar);
    
    // Iframe wrapper
    this.iframeWrapper = document.createElement('div');
    this.iframeWrapper.className = 'design-canvas-iframe-wrapper';
    this.iframeWrapper.style.cssText = `
      flex: 1;
      position: relative;
      overflow: hidden;
      background: white;
    `;
    
    // Create sandboxed iframe
    this.iframe = document.createElement('iframe');
    this.iframe.sandbox = 'allow-scripts allow-same-origin allow-forms allow-pointer-lock';
    this.iframe.style.cssText = `
      width: 100%;
      height: 100%;
      border: none;
      background: white;
    `;
    this.iframeWrapper.appendChild(this.iframe);
    this.canvasContainer.appendChild(this.iframeWrapper);
    
    // Properties panel (inline editor)
    this.propertiesPanel = this.createPropertiesPanel();
    this.canvasContainer.appendChild(this.propertiesPanel);
    
    this.container.appendChild(this.canvasContainer);
    
    // Initialize iframe content
    this.updateIframeContent();
  }
  
  createToolbar() {
    const toolbar = document.createElement('div');
    toolbar.className = 'design-canvas-toolbar';
    toolbar.style.cssText = `
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 12px 16px;
      background: var(--hds-surface, #0a0a0b);
      border-bottom: 1px solid var(--hds-border, rgba(255,255,255,0.08));
      flex-wrap: wrap;
    `;
    
    toolbar.innerHTML = `
      <div class="toolbar-group" style="display: flex; gap: 8px; align-items: center;">
        <button id="canvas-undo" title="Undo (Ctrl+Z)" class="canvas-btn" style="${this.btnStyle()}">↶</button>
        <button id="canvas-redo" title="Redo (Ctrl+Shift+Z)" class="canvas-btn" style="${this.btnStyle()}">↷</button>
      </div>
      <div class="toolbar-divider" style="width: 1px; height: 24px; background: var(--hds-border);"></div>
      <div class="toolbar-group" style="display: flex; gap: 8px; align-items: center;">
        <button id="canvas-add-component" title="Add Component" class="canvas-btn" style="${this.btnStyle()}">+</button>
        <button id="canvas-voice" title="Voice Command (Hold Space)" class="canvas-btn canvas-btn-primary" style="${this.btnStyle({primary: true})}">🎤</button>
        <button id="canvas-snapshot" title="Save Snapshot" class="canvas-btn" style="${this.btnStyle()}">📸</button>
      </div>
      <div class="toolbar-divider" style="width: 1px; height: 24px; background: var(--hds-border);"></div>
      <div class="toolbar-group" style="display: flex; gap: 8px; align-items: center; margin-left: auto;">
        <select id="canvas-theme" style="padding: 6px 10px; border-radius: 6px; border: 1px solid var(--hds-border); background: var(--hds-bg); color: var(--hds-fg);">
          <option value="auto">Auto</option>
          <option value="dark">Dark</option>
          <option value="light">Light</option>
        </select>
        <button id="canvas-export" title="Export" class="canvas-btn" style="${this.btnStyle()}">⬇</button>
        <button id="canvas-fullscreen" title="Fullscreen" class="canvas-btn" style="${this.btnStyle()}">⛶</button>
      </div>
    `;
    
    // Event listeners
    toolbar.querySelector('#canvas-undo').addEventListener('click', () => this.undo());
    toolbar.querySelector('#canvas-redo').addEventListener('click', () => this.redo());
    toolbar.querySelector('#canvas-add-component').addEventListener('click', () => this.showComponentPicker());
    toolbar.querySelector('#canvas-voice').addEventListener('mousedown', () => this.startVoiceRecording());
    toolbar.querySelector('#canvas-voice').addEventListener('mouseup', () => this.stopVoiceRecording());
    toolbar.querySelector('#canvas-voice').addEventListener('mouseleave', () => this.stopVoiceRecording());
    toolbar.querySelector('#canvas-snapshot').addEventListener('click', () => this.saveSnapshot());
    toolbar.querySelector('#canvas-theme').addEventListener('change', (e) => this.setTheme(e.target.value));
    toolbar.querySelector('#canvas-export').addEventListener('click', () => this.showExportModal());
    toolbar.querySelector('#canvas-fullscreen').addEventListener('click', () => this.toggleFullscreen());
    
    return toolbar;
  }
  
  btnStyle(opts = {}) {
    const base = `
      padding: 8px 12px;
      border-radius: 6px;
      border: 1px solid var(--hds-border);
      background: var(--hds-card, rgba(255,255,255,0.04));
      color: var(--hds-fg);
      cursor: pointer;
      font-size: 14px;
      transition: all 0.15s;
    `;
    if (opts.primary) {
      return base + `background: var(--hds-accent); color: var(--hds-bg); border-color: var(--hds-accent);`;
    }
    return base;
  }
  
  createDeviceToolbar() {
    const toolbar = document.createElement('div');
    toolbar.className = 'design-canvas-device-toolbar';
    toolbar.style.cssText = `
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 16px;
      background: var(--hds-surface);
      border-bottom: 1px solid var(--hds-border);
      overflow-x: auto;
    `;
    
    const devices = [
      { id: 'desktop', label: 'Desktop', width: '100%', height: '100%', icon: '🖥' },
      { id: 'tablet', label: 'Tablet', width: '768px', height: '1024px', icon: '📱' },
      { id: 'mobile', label: 'Mobile', width: '375px', height: '667px', icon: '📱' },
      { id: 'mobile-landscape', label: 'Mobile ▸', width: '667px', height: '375px', icon: '📱' },
      { id: 'custom', label: 'Custom', width: 'custom', height: 'custom', icon: '✏️' }
    ];
    
    toolbar.innerHTML = devices.map(d => `
      <button data-device="${d.id}" class="device-btn" style="${this.btnStyle()}" title="${d.label}">
        ${d.icon} ${d.label}
      </button>
    `).join('');
    
    toolbar.querySelectorAll('.device-btn').forEach(btn => {
      btn.addEventListener('click', () => this.setDevice(btn.dataset.device));
    });
    
    this.currentDevice = 'desktop';
    this.updateDeviceToolbar();
    
    return toolbar;
  }
  
  updateDeviceToolbar() {
    this.deviceToolbar.querySelectorAll('.device-btn').forEach(btn => {
      const isActive = btn.dataset.device === this.currentDevice;
      btn.style.background = isActive ? 'var(--hds-accent)' : '';
      btn.style.color = isActive ? 'var(--hds-bg)' : '';
      btn.style.borderColor = isActive ? 'var(--hds-accent)' : '';
    });
  }
  
  setDevice(deviceId) {
    this.currentDevice = deviceId;
    this.updateDeviceToolbar();
    this.applyDeviceSize(deviceId);
  }
  
  applyDeviceSize(deviceId) {
    const sizes = {
      desktop: { width: '100%', height: '100%', borderRadius: '0' },
      tablet: { width: '768px', height: '1024px', borderRadius: '12px' },
      mobile: { width: '375px', height: '667px', borderRadius: '24px' },
      'mobile-landscape': { width: '667px', height: '375px', borderRadius: '16px' }
    };
    
    const size = sizes[deviceId] || sizes.desktop;
    this.iframeWrapper.style.width = size.width;
    this.iframeWrapper.style.height = size.height;
    this.iframeWrapper.style.borderRadius = size.borderRadius;
    this.iframeWrapper.style.margin = deviceId === 'desktop' ? '0' : '20px auto';
    this.iframeWrapper.style.boxShadow = deviceId === 'desktop' ? 'none' : '0 20px 60px rgba(0,0,0,0.4)';
    this.iframeWrapper.style.background = deviceId === 'desktop' ? 'transparent' : 'white';
  }
  
  createPropertiesPanel() {
    const panel = document.createElement('div');
    panel.className = 'design-canvas-properties';
    panel.style.cssText = `
      position: absolute;
      right: 0;
      top: 0;
      bottom: 0;
      width: 320px;
      background: var(--hds-surface);
      border-left: 1px solid var(--hds-border);
      padding: 16px;
      overflow-y: auto;
      transform: translateX(100%);
      transition: transform 0.25s ease;
      z-index: 100;
      display: none;
    `;
    panel.innerHTML = `
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
        <h3 style="margin: 0; font-size: 14px; font-weight: 600;">Properties</h3>
        <button id="props-close" style="${this.btnStyle()}">✕</button>
      </div>
      <div id="props-content">Select an element to edit</div>
    `;
    
    panel.querySelector('#props-close').addEventListener('click', () => this.hidePropertiesPanel());
    
    return panel;
  }
  
  setupMessageChannel() {
    // Wait for iframe to load
    this.iframe.onload = () => {
      // Create MessageChannel for secure communication
      this.messageChannel = new MessageChannel();
      this.port = this.messageChannel.port1;
      
      // Send port to iframe
      this.iframe.contentWindow.postMessage({
        type: 'INIT_CHANNEL',
        designTokens: this.designTokens,
        themeCSS: this.themeCSS
      }, '*', [this.messageChannel.port2]);
      
      // Listen for messages from iframe
      this.port.onmessage = (event) => this.handleIframeMessage(event.data);
      this.port.start();
    };
  }
  
  handleIframeMessage(data) {
    switch (data.type) {
      case 'ELEMENT_SELECTED':
        this.showPropertiesPanel(data.element);
        break;
      case 'ELEMENT_UPDATED':
        this.onElementUpdated(data);
        break;
      case 'CONSOLE_LOG':
        console.log('[Canvas]', ...data.args);
        break;
      case 'CONSOLE_ERROR':
        console.error('[Canvas]', ...data.args);
        break;
      case 'READY':
        console.log('[DesignCanvas] Iframe ready');
        break;
    }
  }
  
  sendToIframe(message) {
    if (this.port) {
      this.port.postMessage(message);
    }
  }
  
  updateIframeContent(html = null) {
    const content = html || this.generatePrototypeHTML();
    this.iframe.srcdoc = content;
  }
  
  generatePrototypeHTML() {
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Hermes Design Canvas Preview</title>
  <style>
    ${this.themeCSS}
    * { box-sizing: border-box; }
    html, body { 
      margin: 0; padding: 0; height: 100%; 
      font-family: var(--hds-font-sans, system-ui, sans-serif);
      background: var(--hds-bg, white);
      color: var(--hds-fg, #1a1a1a);
    }
    body { display: flex; flex-direction: column; }
    [data-canvas-element] { 
      position: relative; 
      outline: none; 
    }
    [data-canvas-element]:hover { 
      outline: 2px solid var(--hds-accent); 
      outline-offset: 2px; 
    }
    [data-canvas-element].selected { 
      outline: 2px solid var(--hds-accent); 
      outline-offset: 2px; 
    }
    .canvas-drop-zone { 
      min-height: 100px; 
      border: 2px dashed var(--hds-border); 
      border-radius: 8px; 
      display: flex; 
      align-items: center; 
      justify-content: center; 
      color: var(--hds-muted); 
      font-size: 14px;
    }
    .canvas-drop-zone.active { 
      border-color: var(--hds-accent); 
      background: rgba(var(--hds-accent-rgb, 255,140,0), 0.1); 
    }
  </style>
</head>
<body>
  <div id="canvas-root" data-canvas-root>
    <div class="canvas-drop-zone" data-drop-zone>
      Drop components here or click + to add
    </div>
  </div>
  
  <script>
    // Secure message channel setup
    let port = null;
    let selectedElement = null;
    let designTokens = {};
    let componentRegistry = {};
    
    window.addEventListener('message', (event) => {
      if (event.data.type === 'INIT_CHANNEL' && event.ports[0]) {
        port = event.ports[0];
        designTokens = event.data.designTokens || {};
        componentRegistry = event.data.componentRegistry || {};
        
        port.onmessage = (e) => handleMessage(e.data);
        port.start();
        
        // Notify parent we're ready
        port.postMessage({ type: 'READY' });
        
        setupCanvas();
      }
    });
    
    function handleMessage(data) {
      switch (data.type) {
        case 'UPDATE_CONTENT':
          document.getElementById('canvas-root').innerHTML = data.html;
          setupCanvas();
          break;
        case 'APPLY_TOKENS':
          designTokens = data.tokens;
          applyTokensToRoot();
          break;
        case 'SET_THEME':
          document.documentElement.setAttribute('data-theme', data.theme);
          break;
      }
    }
    
    function sendToParent(message) {
      if (port) port.postMessage(message);
    }
    
    function applyTokensToRoot() {
      const root = document.documentElement;
      Object.entries(designTokens).forEach(([name, token]) => {
        if (token.type === 'color') {
          root.style.setProperty('--hds-' + name.replace(/[^a-z0-9]/gi, '-'), token.value);
        }
      });
    }
    
    function setupCanvas() {
      const root = document.getElementById('canvas-root');
      
      // Make all elements selectable
      root.querySelectorAll('[data-canvas-element], [data-drop-zone]').forEach(el => {
        el.addEventListener('click', (e) => {
          e.stopPropagation();
          selectElement(el);
        });
        
        // Drag and drop
        el.addEventListener('dragover', (e) => {
          e.preventDefault();
          if (el.dataset.dropZone) el.classList.add('active');
        });
        el.addEventListener('dragleave', (e) => {
          if (el.dataset.dropZone) el.classList.remove('active');
        });
        el.addEventListener('drop', (e) => {
          e.preventDefault();
          if (el.dataset.dropZone) el.classList.remove('active');
          const componentType = e.dataTransfer.getData('text/component-type');
          if (componentType) {
            insertComponent(componentType, el);
          }
        });
      });
      
      // Click outside to deselect
      document.addEventListener('click', (e) => {
        if (!e.target.closest('[data-canvas-element], [data-drop-zone]')) {
          deselectElement();
        }
      });
    }
    
    function selectElement(el) {
      if (selectedElement) {
        selectedElement.classList.remove('selected');
      }
      selectedElement = el;
      el.classList.add('selected');
      
      // Send element data to parent
      const rect = el.getBoundingClientRect();
      const computed = getComputedStyle(el);
      
      sendToParent({
        type: 'ELEMENT_SELECTED',
        element: {
          tagName: el.tagName.toLowerCase(),
          id: el.id,
          className: el.className,
          dataset: Object.fromEntries(Object.entries(el.dataset)),
          styles: getRelevantStyles(computed),
          rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
          outerHTML: el.outerHTML.substring(0, 5000)
        }
      });
    }
    
    function deselectElement() {
      if (selectedElement) {
        selectedElement.classList.remove('selected');
        selectedElement = null;
        sendToParent({ type: 'ELEMENT_DESELECTED' });
      }
    }
    
    function getRelevantStyles(computed) {
      const relevant = [
        'display', 'position', 'flexDirection', 'justifyContent', 'alignItems',
        'gap', 'padding', 'margin', 'width', 'height', 'minWidth', 'minHeight',
        'maxWidth', 'maxHeight', 'backgroundColor', 'color', 'fontSize', 'fontWeight',
        'fontFamily', 'borderRadius', 'border', 'boxShadow', 'opacity', 'zIndex',
        'gridTemplateColumns', 'gridTemplateRows', 'gridGap'
      ];
      const styles = {};
      relevant.forEach(prop => {
        const value = computed[prop];
        if (value && value !== 'auto' && value !== 'normal' && value !== '0px' && value !== 'none') {
          styles[prop] = value;
        }
      });
      return styles;
    }
    
    function insertComponent(type, target) {
      const component = createComponent(type);
      if (!component) return;
      
      if (target.dataset.dropZone) {
        // Replace drop zone
        target.replaceWith(component);
      } else {
        // Insert after
        target.insertAdjacentElement('afterend', component);
      }
      
      setupCanvas();
      selectElement(component);
      
      // Notify parent of change
      sendToParent({
        type: 'ELEMENT_UPDATED',
        action: 'insert',
        html: document.getElementById('canvas-root').innerHTML
      });
    }
    
    function createComponent(type) {
      const templates = {
        'button': '<button class="canvas-btn" data-canvas-element data-component="button" style="padding: 12px 24px; background: var(--hds-accent); color: var(--hds-bg); border: none; border-radius: 8px; font-weight: 500; cursor: pointer;">Button</button>',
        'card': '<div class="canvas-card" data-canvas-element data-component="card" style="padding: 24px; background: var(--hds-card); border: 1px solid var(--hds-border); border-radius: 12px;"><h3 style="margin: 0 0 8px;">Card Title</h3><p style="margin: 0; color: var(--hds-muted);">Card description goes here</p></div>',
        'input': '<input type="text" class="canvas-input" data-canvas-element data-component="input" placeholder="Enter text..." style="padding: 12px 16px; background: var(--hds-bg); border: 1px solid var(--hds-border); border-radius: 8px; color: var(--hds-fg); width: 100%;">',
        'heading': '<h1 data-canvas-element data-component="heading" style="margin: 0; font-size: 2rem; font-weight: 600;">Heading</h1>',
        'text': '<p data-canvas-element data-component="text" style="margin: 0; line-height: 1.6; color: var(--hds-fg);">Paragraph text goes here. Edit me!</p>',
        'image': '<div data-canvas-element data-component="image" style="aspect-ratio: 16/9; background: var(--hds-card); border: 1px solid var(--hds-border); border-radius: 8px; display: flex; align-items: center; justify-content: center; color: var(--hds-muted);">[Image]</div>',
        'container': '<div class="canvas-container" data-canvas-element data-component="container" data-drop-zone style="display: flex; flex-direction: column; gap: 16px; padding: 16px; background: var(--hds-card); border: 1px solid var(--hds-border); border-radius: 12px;"><div class="canvas-drop-zone" data-drop-zone>Drop components here</div></div>',
        'grid': '<div class="canvas-grid" data-canvas-element data-component="grid" data-drop-zone style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; padding: 16px; background: var(--hds-card); border: 1px solid var(--hds-border); border-radius: 12px;"><div class="canvas-drop-zone" data-drop-zone>Grid item</div><div class="canvas-drop-zone" data-drop-zone>Grid item</div><div class="canvas-drop-zone" data-drop-zone>Grid item</div></div>'
      };
      
      const div = document.createElement('div');
      div.innerHTML = templates[type] || templates.container;
      return div.firstElementChild;
    }
    
    // Initialize
    applyTokensToRoot();
  </script>
</body></html>`;
  }
  
  setupVoiceCommands() {
    this.recognition = null;
    this.isListening = false;
    
    if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
      const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      this.recognition = new SpeechRecognition();
      this.recognition.continuous = false;
      this.recognition.interimResults = true;
      this.recognition.lang = 'pt-BR';
      
      this.recognition.onresult = (event) => {
        const transcript = Array.from(event.results)
          .map(r => r[0].transcript)
          .join('');
        
        if (event.results[0].isFinal) {
          this.processVoiceCommand(transcript);
        }
      };
      
      this.recognition.onerror = (e) => {
        console.warn('[DesignCanvas] Voice recognition error:', e.error);
        this.isListening = false;
        this.updateVoiceButtonState();
      };
      
      this.recognition.onend = () => {
        this.isListening = false;
        this.updateVoiceButtonState();
      };
    }
  }
  
  startVoiceRecording() {
    if (!this.recognition) {
      alert('Voice recognition not supported in this browser');
      return;
    }
    
    if (this.isListening) return;
    
    this.isListening = true;
    this.updateVoiceButtonState();
    this.recognition.start();
  }
  
  stopVoiceRecording() {
    if (!this.isListening) return;
    this.recognition.stop();
  }
  
  updateVoiceButtonState() {
    const btn = this.toolbar.querySelector('#canvas-voice');
    if (btn) {
      btn.style.background = this.isListening ? '#c0392b' : '';
      btn.style.animation = this.isListening ? 'pulse 1s infinite' : 'none';
      btn.textContent = this.isListening ? '⏹' : '🎤';
    }
  }
  
  async processVoiceCommand(transcript) {
    console.log('[DesignCanvas] Voice command:', transcript);
    
    // Send to bridge for intent parsing
    try {
      const res = await fetch(`${this.bridgeUrl}/api/design/parse-intent`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ transcript })
      });
      const intent = await res.json();
      
      if (intent.action) {
        this.executeIntent(intent);
      }
    } catch (e) {
      console.error('[DesignCanvas] Voice command failed:', e);
      // Fallback: simple local parsing
      this.parseLocalIntent(transcript);
    }
  }
  
  parseLocalIntent(transcript) {
    const lower = transcript.toLowerCase();
    
    // Theme changes
    if (lower.includes('escuro') || lower.includes('dark mode') || lower.includes('modo escuro')) {
      this.setTheme('dark');
      return;
    }
    if (lower.includes('claro') || lower.includes('light mode') || lower.includes('modo claro')) {
      this.setTheme('light');
      return;
    }
    
    // Component insertion
    const componentMap = {
      'botão': 'button', 'button': 'button',
      'card': 'card', 'cartão': 'card',
      'input': 'input', 'campo': 'input', 'caixa de texto': 'input',
      'título': 'heading', 'heading': 'heading', 'title': 'heading',
      'texto': 'text', 'parágrafo': 'text', 'paragraph': 'text',
      'imagem': 'image', 'image': 'image',
      'container': 'container', 'caixa': 'container', 'box': 'container',
      'grade': 'grid', 'grid': 'grid'
    };
    
    for (const [keyword, type] of Object.entries(componentMap)) {
      if (lower.includes(keyword) && (lower.includes('adicione') || lower.includes('crie') || lower.includes('insira') || lower.includes('add') || lower.includes('create'))) {
        this.insertComponent(type);
        return;
      }
    }
    
    // Style changes
    if (lower.includes('cor') || lower.includes('color')) {
      // Would need color parsing
    }
  }
  
  executeIntent(intent) {
    switch (intent.action) {
      case 'setTheme':
        this.setTheme(intent.params.theme);
        break;
      case 'insertComponent':
        this.insertComponent(intent.params.type);
        break;
      case 'updateStyle':
        this.updateSelectedElementStyle(intent.params.property, intent.params.value);
        break;
      case 'deleteElement':
        this.deleteSelectedElement();
        break;
      case 'duplicateElement':
        this.duplicateSelectedElement();
        break;
    }
  }
  
  setTheme(theme) {
    this.sendToIframe({ type: 'SET_THEME', theme });
    this.toolbar.querySelector('#canvas-theme').value = theme;
  }
  
  insertComponent(type) {
    this.sendToIframe({ type: 'INSERT_COMPONENT', componentType: type });
  }
  
  updateSelectedElementStyle(property, value) {
    this.sendToIframe({ type: 'UPDATE_STYLE', property, value });
  }
  
  deleteSelectedElement() {
    this.sendToIframe({ type: 'DELETE_ELEMENT' });
  }
  
  duplicateSelectedElement() {
    this.sendToIframe({ type: 'DUPLICATE_ELEMENT' });
  }
  
  showPropertiesPanel(elementData) {
    this.propertiesPanel.style.display = 'block';
    requestAnimationFrame(() => {
      this.propertiesPanel.style.transform = 'translateX(0)';
    });
    
    const content = this.propertiesPanel.querySelector('#props-content');
    content.innerHTML = this.renderPropertiesForm(elementData);
    
    // Bind inputs
    content.querySelectorAll('input, select').forEach(input => {
      input.addEventListener('change', (e) => {
        this.updateSelectedElementStyle(e.target.dataset.prop, e.target.value);
      });
      input.addEventListener('input', (e) => {
        if (e.target.type === 'range' || e.target.type === 'color') {
          this.updateSelectedElementStyle(e.target.dataset.prop, e.target.value);
        }
      });
    });
  }
  
  renderPropertiesForm(element) {
    if (!element) return '<p style="color: var(--hds-muted);">Select an element</p>';
    
    const styles = element.styles || {};
    const commonProps = [
      { key: 'display', label: 'Display', type: 'select', options: ['block', 'flex', 'grid', 'inline-block', 'inline-flex'] },
      { key: 'flexDirection', label: 'Flex Direction', type: 'select', options: ['row', 'column', 'row-reverse', 'column-reverse'] },
      { key: 'justifyContent', label: 'Justify Content', type: 'select', options: ['flex-start', 'center', 'flex-end', 'space-between', 'space-around', 'space-evenly'] },
      { key: 'alignItems', label: 'Align Items', type: 'select', options: ['stretch', 'flex-start', 'center', 'flex-end', 'baseline'] },
      { key: 'gap', label: 'Gap', type: 'text', unit: 'px' },
      { key: 'padding', label: 'Padding', type: 'text', unit: 'px' },
      { key: 'margin', label: 'Margin', type: 'text', unit: 'px' },
      { key: 'width', label: 'Width', type: 'text', unit: 'px' },
      { key: 'height', label: 'Height', type: 'text', unit: 'px' },
      { key: 'backgroundColor', label: 'Background', type: 'color' },
      { key: 'color', label: 'Text Color', type: 'color' },
      { key: 'fontSize', label: 'Font Size', type: 'text', unit: 'px' },
      { key: 'fontWeight', label: 'Font Weight', type: 'select', options: ['normal', 'medium', 'semibold', 'bold', '100', '200', '300', '400', '500', '600', '700', '800', '900'] },
      { key: 'borderRadius', label: 'Border Radius', type: 'text', unit: 'px' },
      { key: 'border', label: 'Border', type: 'text' },
      { key: 'boxShadow', label: 'Box Shadow', type: 'text' },
      { key: 'opacity', label: 'Opacity', type: 'range', min: 0, max: 1, step: 0.1 }
    ];
    
    let html = `
      <div style="margin-bottom: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--hds-border);">
        <strong>${element.tagName}</strong>
        ${element.id ? `<code style="font-size: 11px; color: var(--hds-muted); margin-left: 8px;">#${element.id}</code>` : ''}
        ${element.className ? `<code style="font-size: 11px; color: var(--hds-muted); margin-left: 8px;">.${element.className.split(' ').join(' .')}</code>` : ''}
      </div>
    `;
    
    commonProps.forEach(prop => {
      const value = styles[prop.key] || '';
      if (!value && prop.type !== 'color' && prop.type !== 'range') return;
      
      let inputHtml = '';
      const propName = prop.key;
      
      switch (prop.type) {
        case 'select':
          inputHtml = `<select data-prop="${propName}" style="width: 100%; padding: 8px; border-radius: 6px; border: 1px solid var(--hds-border); background: var(--hds-bg); color: var(--hds-fg);">
            ${prop.options.map(opt => `<option value="${opt}" ${value === opt ? 'selected' : ''}>${opt}</option>`).join('')}
          </select>`;
          break;
        case 'color':
          inputHtml = `<input type="color" data-prop="${propName}" value="${value || '#ff8c00'}" style="width: 100%; height: 36px; border: none; border-radius: 6px; cursor: pointer;">`;
          break;
        case 'range':
          inputHtml = `<input type="range" data-prop="${propName}" min="${prop.min}" max="${prop.max}" step="${prop.step}" value="${value || prop.min}" style="width: 100%;">`;
          break;
        default:
          inputHtml = `<input type="text" data-prop="${propName}" value="${value}" placeholder="${prop.unit || ''}" style="width: 100%; padding: 8px 10px; border-radius: 6px; border: 1px solid var(--hds-border); background: var(--hds-bg); color: var(--hds-fg);">`;
      }
      
      html += `
        <div style="margin-bottom: 12px;">
          <label style="display: block; font-size: 12px; color: var(--hds-muted); margin-bottom: 4px;">${prop.label}</label>
          ${inputHtml}
        </div>
      `;
    });
    
    html += `
      <div style="display: flex; gap: 8px; margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--hds-border);">
        <button data-action="duplicate" style="${this.btnStyle()} flex: 1;">Duplicate</button>
        <button data-action="delete" style="${this.btnStyle()} flex: 1; background: var(--hds-danger, #ef4444); border-color: var(--hds-danger);">Delete</button>
      </div>
    `;
    
    return html;
  }
  
  hidePropertiesPanel() {
    this.propertiesPanel.style.transform = 'translateX(100%)';
    setTimeout(() => {
      this.propertiesPanel.style.display = 'none';
    }, 250);
  }
  
  onElementUpdated(data) {
    // Save to history
    this.saveToHistory(data.html);
  }
  
  saveToHistory(html) {
    // Trim history after current index
    this.snapshotHistory = this.snapshotHistory.slice(0, this.currentSnapshotIndex + 1);
    this.snapshotHistory.push({ html, timestamp: Date.now() });
    this.currentSnapshotIndex = this.snapshotHistory.length - 1;
    
    // Limit history
    if (this.snapshotHistory.length > 50) {
      this.snapshotHistory.shift();
      this.currentSnapshotIndex--;
    }
  }
  
  undo() {
    if (this.currentSnapshotIndex > 0) {
      this.currentSnapshotIndex--;
      const snapshot = this.snapshotHistory[this.currentSnapshotIndex];
      this.sendToIframe({ type: 'UPDATE_CONTENT', html: snapshot.html });
    }
  }
  
  redo() {
    if (this.currentSnapshotIndex < this.snapshotHistory.length - 1) {
      this.currentSnapshotIndex++;
      const snapshot = this.snapshotHistory[this.currentSnapshotIndex];
      this.sendToIframe({ type: 'UPDATE_CONTENT', html: snapshot.html });
    }
  }
  
  saveSnapshot() {
    this.sendToIframe({ type: 'GET_CONTENT' });
    // The response will come via ELEMENT_UPDATED or we add a specific handler
    alert('Snapshot saved! (Implement persistence to bridge)');
  }
  
  showComponentPicker() {
    // Simple modal for component selection
    const modal = document.createElement('div');
    modal.style.cssText = `
      position: fixed; inset: 0; background: rgba(0,0,0,0.7); backdrop-filter: blur(8px);
      display: flex; align-items: center; justify-content: center; z-index: 1000;
    `;
    modal.innerHTML = `
      <div style="background: var(--hds-surface); border: 1px solid var(--hds-border); border-radius: 16px; padding: 24px; max-width: 400px; width: 90%;">
        <h3 style="margin: 0 0 16px;">Add Component</h3>
        <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px;">
          ${['button', 'card', 'input', 'heading', 'text', 'image', 'container', 'grid'].map(type => `
            <button data-type="${type}" style="${this.btnStyle()} padding: 16px; text-align: left; display: flex; flex-direction: column; gap: 4px;">
              <span style="font-weight: 500;">${type.charAt(0).toUpperCase() + type.slice(1)}</span>
              <span style="font-size: 11px; color: var(--hds-muted);">${this.getComponentDescription(type)}</span>
            </button>
          `).join('')}
        </div>
      </div>
    `;
    
    modal.querySelectorAll('[data-type]').forEach(btn => {
      btn.addEventListener('click', () => {
        this.insertComponent(btn.dataset.type);
        document.body.removeChild(modal);
      });
    });
    
    modal.addEventListener('click', (e) => {
      if (e.target === modal) document.body.removeChild(modal);
    });
    
    document.body.appendChild(modal);
  }
  
  getComponentDescription(type) {
    const desc = {
      button: 'Interactive button',
      card: 'Content container',
      input: 'Text input field',
      heading: 'Section title',
      text: 'Paragraph text',
      image: 'Image placeholder',
      container: 'Flex container',
      grid: 'Grid layout'
    };
    return desc[type] || '';
  }
  
  showExportModal() {
    const modal = document.createElement('div');
    modal.style.cssText = `
      position: fixed; inset: 0; background: rgba(0,0,0,0.7); backdrop-filter: blur(8px);
      display: flex; align-items: center; justify-content: center; z-index: 1000;
    `;
    modal.innerHTML = `
      <div style="background: var(--hds-surface); border: 1px solid var(--hds-border); border-radius: 16px; padding: 24px; max-width: 480px; width: 90%;">
        <h3 style="margin: 0 0 16px;">Export Prototype</h3>
        <div style="display: flex; flex-direction: column; gap: 12px;">
          <button data-format="html" style="${this.btnStyle()} display: flex; align-items: center; gap: 12px; padding: 16px; text-align: left;">
            <span style="font-size: 24px;">🌐</span>
            <div>
              <strong>HTML Bundle</strong><br>
              <small style="color: var(--hds-muted);">Self-contained HTML with tokens & components</small>
            </div>
          </button>
          <button data-format="code-bundle" style="${this.btnStyle()} display: flex; align-items: center; gap: 12px; padding: 16px; text-align: left;">
            <span style="font-size: 24px;">📦</span>
            <div>
              <strong>Code Bundle</strong><br>
              <small style="color: var(--hds-muted);">React/Vue/Svelte components + Tailwind config</small>
            </div>
          </button>
          <button data-format="pptx" style="${this.btnStyle()} display: flex; align-items: center; gap: 12px; padding: 16px; text-align: left;">
            <span style="font-size: 24px;">📊</span>
            <div>
              <strong>PowerPoint</strong><br>
              <small style="color: var(--hds-muted);">Editable slides for presentations</small>
            </div>
          </button>
          <button data-format="figma" style="${this.btnStyle()} display: flex; align-items: center; gap: 12px; padding: 16px; text-align: left;">
            <span style="font-size: 24px;">🎨</span>
            <div>
              <strong>Figma Plugin</strong><br>
              <small style="color: var(--hds-muted);">Send frames to Figma (requires token)</small>
            </div>
          </button>
        </div>
      </div>
    `;
    
    modal.querySelectorAll('[data-format]').forEach(btn => {
      btn.addEventListener('click', () => {
        this.exportPrototype(btn.dataset.format);
        document.body.removeChild(modal);
      });
    });
    
    modal.addEventListener('click', (e) => {
      if (e.target === modal) document.body.removeChild(modal);
    });
    
    document.body.appendChild(modal);
  }
  
  async exportPrototype(format) {
    this.sendToIframe({ type: 'GET_CONTENT' });
    
    // Wait for content response (simplified)
    setTimeout(async () => {
      try {
        const res = await fetch(`${this.bridgeUrl}/api/design/export/${format}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ 
            html: this.lastContent || '',
            tokens: this.designTokens,
            components: this.componentRegistry
          })
        });
        
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `hermes-prototype.${format === 'code-bundle' ? 'zip' : format}`;
        a.click();
        URL.revokeObjectURL(url);
      } catch (e) {
        console.error('Export failed:', e);
        alert('Export failed: ' + e.message);
      }
    }, 100);
  }
  
  setupKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
      // Only when canvas is focused
      if (!this.canvasContainer.contains(document.activeElement) && 
          document.activeElement !== this.iframe) return;
      
      if ((e.ctrlKey || e.metaKey) && e.key === 'z') {
        e.preventDefault();
        if (e.shiftKey) this.redo();
        else this.undo();
      }
      if ((e.ctrlKey || e.metaKey) && e.key === 'y') {
        e.preventDefault();
        this.redo();
      }
      if (e.code === 'Space' && !e.target.matches('input, textarea, select')) {
        e.preventDefault();
        this.startVoiceRecording();
      }
      if (e.key === 'Delete' || e.key === 'Backspace') {
        this.deleteSelectedElement();
      }
      if ((e.ctrlKey || e.metaKey) && e.key === 'd') {
        e.preventDefault();
        this.duplicateSelectedElement();
      }
      if (e.key === 'Escape') {
        this.hidePropertiesPanel();
      }
    });
    
    document.addEventListener('keyup', (e) => {
      if (e.code === 'Space') {
        this.stopVoiceRecording();
      }
    });
  }
  
  toggleFullscreen() {
    if (!document.fullscreenElement) {
      this.canvasContainer.requestFullscreen().catch(console.error);
    } else {
      document.exitFullscreen();
    }
  }
}

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
  module.exports = DesignCanvas;
}