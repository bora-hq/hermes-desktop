/**
 * Hermes Collaboration Layer
 * Real-time collaborative editing with Yjs + WebRTC
 * Features: Presence, Comments, Versioning, Invite links
 */

import * as Y from 'yjs';
import { WebrtcProvider } from 'y-webrtc';
import { IndexeddbPersistence } from 'y-indexeddb';

class CollaborationManager {
  constructor(options = {}) {
    this.bridgeUrl = options.bridgeUrl || 'http://127.0.0.1:8420';
    this.roomName = options.roomName || 'hermes-design';
    this.userId = options.userId || this.generateUserId();
    this.userName = options.userName || `User-${this.userId.slice(0, 4)}`;
    this.userColor = options.userColor || this.generateColor();
    
    this.doc = null;
    this.provider = null;
    this.persistence = null;
    this.awareness = null;
    
    this.ydoc = null;
    this.ytext = null;
    this.ycomments = null;
    this.ypresence = null;
    this.yversion = null;
    
    this.isConnected = false;
    this.collaborators = new Map();
    this.commentThreads = new Map();
    
    this.init();
  }
  
  generateUserId() {
    return 'user-' + Math.random().toString(36).substr(2, 9) + Date.now().toString(36);
  }
  
  generateColor() {
    const colors = [
      '#ff8c00', '#22c55e', '#3b82f6', '#ec4899', '#8b5cf6',
      '#f59e0b', '#06b6d4', '#ef4444', '#10b981', '#f97316'
    ];
    return colors[Math.floor(Math.random() * colors.length)];
  }
  
  async init() {
    // Initialize Yjs document
    this.doc = new Y.Doc();
    
    // Shared text for canvas content
    this.ytext = this.doc.getText('canvas-content');
    
    // Comments map: threadId -> Y.Map({ id, author, text, timestamp, resolved, position })
    this.ycomments = this.doc.getMap('comments');
    
    // Presence awareness
    this.awareness = this.provider?.awareness || null;
    
    // Version history
    this.yversion = this.doc.getArray('version-history');
    
    // Set up IndexedDB persistence for offline-first
    this.persistence = new IndexeddbPersistence(`hermes-collab-${this.roomName}`, this.doc);
    await this.persistence.whenSynced;
    
    // Set up WebRTC provider for P2P
    this.provider = new WebrtcProvider(this.roomName, this.doc, {
      signaling: ['wss://signaling.yjs.dev', 'wss://signaling.yjs.in'],
      maxConns: 20,
      filterBcConns: false
    });
    
    this.awareness = this.provider.awareness;
    this.setupAwareness();
    this.setupEventListeners();
    
    // Sync with bridge for relay server (for persistence + auth)
    await this.syncWithBridge();
  }
  
  setupAwareness() {
    // Set local user state
    this.awareness.setLocalState({
      user: {
        id: this.userId,
        name: this.userName,
        color: this.userColor,
        cursor: null,
        selection: null,
        lastActive: Date.now()
      }
    });
    
    // Listen for other users
    this.awareness.on('change', () => {
      this.updateCollaborators();
    });
  }
  
  updateCollaborators() {
    const states = this.awareness.getStates();
    const newCollaborators = new Map();
    
    states.forEach((state, clientId) => {
      if (clientId !== this.awareness.clientId && state.user) {
        newCollaborators.set(clientId, {
          ...state.user,
          clientId
        });
      }
    });
    
    this.collaborators = newCollaborators;
    this.emit('collaborators-changed', Array.from(this.collaborators.values()));
  }
  
  setupEventListeners() {
    // Document changes
    this.doc.on('update', (update) => {
      this.emit('document-update', update);
    });
    
    // Text changes
    this.ytext.observe(() => {
      this.emit('text-change', this.ytext.toString());
    });
    
    // Comments changes
    this.ycomments.observe((event) => {
      event.changes.keys.forEach((change, key) => {
        if (change.action === 'add' || change.action === 'update') {
          const comment = this.ycomments.get(key);
          this.emit('comment-change', { key, comment, action: change.action });
        } else if (change.action === 'delete') {
          this.emit('comment-delete', { key });
        }
      });
    });
    
    // Connection status
    this.provider.on('status', (event) => {
      this.isConnected = event.status === 'connected';
      this.emit('connection-status', { connected: this.isConnected, peers: event.peers });
    });
    
    // Peer connections
    this.provider.on('peer', (peerId, type) => {
      this.emit('peer-change', { peerId, type }); // 'join' | 'leave'
    });
  }
  
  async syncWithBridge() {
    try {
      // Fetch existing document from bridge
      const res = await fetch(`${this.bridgeUrl}/api/collab/document/${this.roomName}`);
      if (res.ok) {
        const data = await res.json();
        if (data.content) {
          // Apply as initial content
          this.doc.transact(() => {
            this.ytext.delete(0, this.ytext.length);
            this.ytext.insert(0, data.content);
          });
        }
        if (data.comments) {
          Object.entries(data.comments).forEach(([key, value]) => {
            this.ycomments.set(key, value);
          });
        }
      }
    } catch (e) {
      console.warn('[Collab] Bridge sync failed:', e);
    }
  }
  
  // Public API
  
  // Text editing
  insertText(index, text) {
    this.doc.transact(() => {
      this.ytext.insert(index, text);
    });
  }
  
  deleteText(index, length) {
    this.doc.transact(() => {
      this.ytext.delete(index, length);
    });
  }
  
  getText() {
    return this.ytext.toString();
  }
  
  // Comments
  addComment(threadId, comment) {
    const id = `comment-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    const commentData = {
      id,
      threadId,
      author: { id: this.userId, name: this.userName, color: this.userColor },
      text: comment.text,
      timestamp: Date.now(),
      resolved: false,
      position: comment.position // { x, y, elementId }
    };
    
    const thread = this.ycomments.get(threadId) || { comments: [], resolved: false };
    thread.comments.push(commentData);
    thread.resolved = false;
    
    this.doc.transact(() => {
      this.ycomments.set(threadId, thread);
    });
    
    return id;
  }
  
  resolveThread(threadId) {
    const thread = this.ycomments.get(threadId);
    if (thread) {
      this.doc.transact(() => {
        thread.resolved = true;
        this.ycomments.set(threadId, thread);
      });
    }
  }
  
  getComments() {
    const comments = {};
    this.ycomments.forEach((value, key) => {
      comments[key] = value;
    });
    return comments;
  }
  
  // Presence
  updateCursor(position) {
    this.awareness.setLocalStateField('user', {
      ...this.awareness.getLocalState()?.user,
      cursor: position,
      lastActive: Date.now()
    });
  }
  
  updateSelection(selection) {
    this.awareness.setLocalStateField('user', {
      ...this.awareness.getLocalState()?.user,
      selection,
      lastActive: Date.now()
    });
  }
  
  getCollaborators() {
    return Array.from(this.collaborators.values());
  }
  
  // Versioning
  createSnapshot(label = '') {
    const snapshot = Y.snapshot(this.doc);
    const version = {
      id: `ver-${Date.now()}`,
      timestamp: Date.now(),
      label,
      snapshot,
      author: { id: this.userId, name: this.userName }
    };
    
    this.doc.transact(() => {
      this.yversion.push([version]);
    });
    
    return version;
  }
  
  getVersions() {
    const versions = [];
    this.yversion.forEach((v) => versions.push(v));
    return versions;
  }
  
  restoreVersion(versionId) {
    const versions = this.getVersions();
    const version = versions.find(v => v.id === versionId);
    if (version) {
      Y.applySnapshot(this.doc, version.snapshot);
      this.emit('version-restored', version);
    }
  }
  
  // Invite links
  generateInviteLink() {
    const baseUrl = window.location.origin;
    return `${baseUrl}/design.html?room=${this.roomName}&invite=${this.userId}`;
  }
  
  // Export/Import
  exportState() {
    const state = Y.encodeStateAsUpdate(this.doc);
    return {
      room: this.roomName,
      state: Array.from(state),
      timestamp: Date.now()
    };
  }
  
  static async importState(exported) {
    const doc = new Y.Doc();
    Y.applyUpdate(doc, new Uint8Array(exported.state));
    return doc;
  }
  
  // Cleanup
  destroy() {
    this.provider?.disconnect();
    this.provider?.destroy();
    this.persistence?.destroy();
    this.doc?.destroy();
  }
  
  // Event emitter
  emit(event, data) {
    window.dispatchEvent(new CustomEvent(`collab:${event}`, { detail: data }));
  }
  
  on(event, callback) {
    window.addEventListener(`collab:${event}`, (e) => callback(e.detail));
  }
}

// Auto-load Yjs from CDN if not available
async function loadYjs() {
  if (window.Y) return;
  
  await Promise.all([
    loadScript('https://cdn.jsdelivr.net/npm/yjs@13.6.8/dist/y.js'),
    loadScript('https://cdn.jsdelivr.net/npm/y-webrtc@10.2.6/dist/y-webrtc.js'),
    loadScript('https://cdn.jsdelivr.net/npm/y-indexeddb@9.0.12/dist/y-indexeddb.js')
  ]);
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = src;
    script.onload = resolve;
    script.onerror = reject;
    document.head.appendChild(script);
  });
}

// Export
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { CollaborationManager, loadYjs };
} else {
  window.CollaborationManager = CollaborationManager;
  window.loadYjs = loadYjs;
}