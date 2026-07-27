"""
Design System Engine for Hermes Voice
Extracts design tokens, components, and patterns from codebases (CSS, HTML, JS, Figma JSON).
Inspired by Claude Design's design system ingestion.
"""

import re
import json
import os
from pathlib import Path
from typing import Dict, List, Any, Optional, Set
from dataclasses import dataclass, asdict, field
from collections import defaultdict
import tinycss2


@dataclass
class DesignToken:
    """A single design token (color, spacing, typography, etc.)"""
    name: str
    value: str
    type: str  # color, spacing, typography, border, shadow, radius, opacity, z-index, other
    source: str  # css, html, js, figma, manual
    file_path: Optional[str] = None
    line: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Component:
    """A reusable UI component with its tokens and variants"""
    name: str
    selector: str  # CSS selector or component name
    tokens: List[str]  # References to design token names
    variants: Dict[str, Dict[str, str]]  # variant_name -> {prop: value}
    states: Dict[str, Dict[str, str]]    # state_name -> {prop: value}
    html_template: Optional[str] = None
    description: str = ""
    file_path: Optional[str] = None


@dataclass
class DesignSystem:
    """Complete extracted design system"""
    tokens: Dict[str, DesignToken] = field(default_factory=dict)
    components: Dict[str, Component] = field(default_factory=dict)
    patterns: Dict[str, Any] = field(default_factory=dict)  # Layout patterns, etc.
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "tokens": {k: asdict(v) for k, v in self.tokens.items()},
            "components": {k: asdict(v) for k, v in self.components.items()},
            "patterns": self.patterns,
            "metadata": self.metadata
        }
    
    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


class TokenExtractor:
    """Extracts design tokens from various sources"""
    
    # Token type detection patterns
    COLOR_PATTERNS = [
        r'^#[0-9a-fA-F]{3,8}$',           # Hex
        r'^rgb\(.+\)$',                    # RGB
        r'^rgba\(.+\)$',                   # RGBA
        r'^hsl\(.+\)$',                    # HSL
        r'^hsla\(.+\)$',                   # HSLA
        r'^(oklch|oklab|lab|lch)\(.+\)$',  # Modern color spaces
    ]
    
    SPACING_PATTERNS = [
        r'^\d+(\.\d+)?(px|rem|em|ch|ex|vw|vh|vmin|vmax|%|cm|mm|in|pt|pc)$',
        r'^calc\(.+\)$',
    ]
    
    TYPOGRAPHY_PATTERNS = [
        r'^(normal|italic|oblique)$',           # font-style
        r'^\d{1,3}$',                            # font-weight numeric
        r'^(normal|bold|bolder|lighter)$',       # font-weight named
        r'^.+$',                                 # font-family (catch-all)
    ]
    
    def __init__(self):
        self.tokens: Dict[str, DesignToken] = {}
        self._token_counter = defaultdict(int)
    
    def _generate_token_name(self, value: str, type_hint: str, prefix: str = "") -> str:
        """Generate a semantic token name from value"""
        self._token_counter[type_hint] += 1
        base = f"{prefix}{type_hint}-{self._token_counter[type_hint]}"
        return base.replace(" ", "-").lower()
    
    def _detect_token_type(self, value: str, property_name: str = "") -> str:
        """Detect token type from value and CSS property"""
        prop = property_name.lower()
        val = value.strip()
        
        # Color properties
        if any(kw in prop for kw in ['color', 'background', 'border', 'outline', 'fill', 'stroke', 'accent']):
            for pattern in self.COLOR_PATTERNS:
                if re.match(pattern, val):
                    return 'color'
        
        # Spacing/sizing properties
        if any(kw in prop for kw in ['margin', 'padding', 'gap', 'width', 'height', 'size', 'space', 'inset', 'top', 'right', 'bottom', 'left']):
            for pattern in self.SPACING_PATTERNS:
                if re.match(pattern, val):
                    return 'spacing'
        
        # Typography properties
        if any(kw in prop for kw in ['font', 'text', 'letter', 'word', 'line', 'white-space']):
            return 'typography'
        
        # Border properties
        if 'border' in prop or 'outline' in prop:
            if 'radius' in prop:
                return 'radius'
            return 'border'
        
        # Shadow
        if 'shadow' in prop:
            return 'shadow'
        
        # Opacity
        if 'opacity' in prop:
            return 'opacity'
        
        # Z-index
        if 'z-index' in prop or 'zindex' in prop:
            return 'z-index'
        
        # Fallback: try to detect by value
        for pattern in self.COLOR_PATTERNS:
            if re.match(pattern, val):
                return 'color'
        for pattern in self.SPACING_PATTERNS:
            if re.match(pattern, val):
                return 'spacing'
        
        return 'other'
    
    def extract_from_css(self, css_content: str, file_path: str = "") -> List[DesignToken]:
        """Extract tokens from CSS using tinycss2"""
        tokens = []
        
        try:
            stylesheet = tinycss2.parse_stylesheet(css_content, skip_comments=True)
            
            for rule in stylesheet:
                if rule.type == 'qualified-rule':
                    # Parse selector and declarations
                    prelude = tinycss2.serialize(rule.prelude).strip()
                    declarations = tinycss2.parse_declaration_list(rule.content)
                    
                    for decl in declarations:
                        if decl.type == 'declaration':
                            prop_name = decl.name
                            value = tinycss2.serialize(decl.value).strip()
                            
                            if value and not value.startswith('var('):  # Skip CSS variables for now
                                token_type = self._detect_token_type(value, prop_name)
                                token_name = self._generate_token_name(value, token_type, f"{prelude}-")
                                
                                token = DesignToken(
                                    name=token_name,
                                    value=value,
                                    type=token_type,
                                    source='css',
                                    file_path=file_path,
                                    metadata={'property': prop_name, 'selector': prelude}
                                )
                                tokens.append(token)
                                self.tokens[token_name] = token
                
                elif rule.type == 'at-rule' and getattr(rule, 'at_keyword', None) == 'property':
                    # CSS @property custom properties
                    pass
                    
        except Exception as e:
            print(f"CSS parse error in {file_path}: {e}")
        
        return tokens
    
    def extract_from_css_variables(self, css_content: str, file_path: str = "") -> List[DesignToken]:
        """Extract CSS custom properties (--*) as tokens"""
        tokens = []
        
        # Match --name: value; patterns
        var_pattern = re.compile(r'(--[\w-]+)\s*:\s*([^;]+);')
        
        for match in var_pattern.finditer(css_content):
            name = match.group(1)
            value = match.group(2).strip()
            
            # Determine type from name and value
            token_type = self._detect_token_type(value, name)
            
            token = DesignToken(
                name=name.lstrip('-').replace('-', '-'),
                value=value,
                type=token_type,
                source='css-variable',
                file_path=file_path,
                metadata={'custom_property': name}
            )
            tokens.append(token)
            self.tokens[token.name] = token
        
        return tokens
    
    def extract_from_html(self, html_content: str, file_path: str = "") -> List[DesignToken]:
        """Extract inline styles and class patterns from HTML"""
        tokens = []
        
        # Extract from <style> blocks
        style_blocks = re.findall(r'<style[^>]*>(.*?)</style>', html_content, re.DOTALL | re.IGNORECASE)
        for css in style_blocks:
            tokens.extend(self.extract_from_css_variables(css, file_path))
            tokens.extend(self.extract_from_css(css, file_path))
        
        # Inline styles
        style_pattern = re.compile(r'style\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
        for match in style_pattern.finditer(html_content):
            inline_css = match.group(1)
            tokens.extend(self.extract_from_css(inline_css, file_path))
        
        return tokens
    
    def extract_from_js(self, js_content: str, file_path: str = "") -> List[DesignToken]:
        """Extract tokens from JS/TS (theme objects, styled-components, etc.)"""
        tokens = []
        
        # Theme object patterns: { colors: { primary: '#...' }, spacing: { 4: '1rem' } }
        theme_pattern = re.compile(
            r'(?:const|let|var|export\s+(?:const|default)?)\s+(\w+)\s*=\s*(\{[\s\S]*?\n\})',
            re.MULTILINE
        )
        
        for match in theme_pattern.finditer(js_content):
            var_name = match.group(1)
            obj_str = match.group(2)
            
            if any(kw in var_name.lower() for kw in ['theme', 'token', 'design', 'style', 'color', 'palette']):
                try:
                    parsed = self._parse_js_object(obj_str)
                    tokens.extend(self._flatten_theme_object(parsed, var_name, file_path))
                except:
                    pass
        
        return tokens
    
    def _parse_js_object(self, obj_str: str) -> Dict:
        """Simple JS object parser for theme objects"""
        obj_str = obj_str.replace("'", '"')
        obj_str = re.sub(r'(\w+):', r'"\1":', obj_str)  # keys to strings
        obj_str = re.sub(r',\s*}', '}', obj_str)  # trailing commas
        obj_str = re.sub(r',\s*]', ']', obj_str)
        return json.loads(obj_str)
    
    def _flatten_theme_object(self, obj: Dict, prefix: str, file_path: str) -> List[DesignToken]:
        """Flatten nested theme object into tokens"""
        tokens = []
        
        def flatten(current: Any, path: List[str]):
            if isinstance(current, dict):
                for k, v in current.items():
                    flatten(v, path + [k])
            elif isinstance(current, list):
                for i, v in enumerate(current):
                    flatten(v, path + [str(i)])
            else:
                # Leaf value
                token_name = '-'.join([prefix] + path)
                token_type = self._detect_token_type(str(current), path[-1] if path else '')
                
                token = DesignToken(
                    name=token_name,
                    value=str(current),
                    type=token_type,
                    source='js-theme',
                    file_path=file_path,
                    metadata={'theme_path': path}
                )
                tokens.append(token)
                self.tokens[token_name] = token
        
        flatten(obj, [])
        return tokens
    
    def extract_from_figma(self, figma_json: Dict, file_path: str = "") -> List[DesignToken]:
        """Extract tokens from Figma JSON export (Design Tokens format)"""
        tokens = []
        
        # Figma Design Tokens format
        if 'tokens' in figma_json:
            for token_group in figma_json['tokens']:
                for token_name, token_data in token_group.items():
                    if isinstance(token_data, dict) and '$value' in token_data:
                        value = token_data['$value']
                        token_type = token_data.get('$type', 'other')
                        
                        token = DesignToken(
                            name=f"figma-{token_group}-{token_name}",
                            value=str(value),
                            type=token_type,
                            source='figma',
                            file_path=file_path,
                            metadata={'figma_group': token_group, 'figma_name': token_name}
                        )
                        tokens.append(token)
                        self.tokens[token.name] = token
        
        return tokens


class ComponentExtractor:
    """Extracts reusable components from codebase"""
    
    def __init__(self, token_extractor: TokenExtractor):
        self.token_extractor = token_extractor
        self.components: Dict[str, Component] = {}
    
    def extract_from_html(self, html_content: str, file_path: str = "") -> List[Component]:
        """Extract component patterns from HTML"""
        components = []
        
        # Look for semantic component structures
        # This is a simplified version - in production use a proper HTML parser
        
        component_patterns = {
            'card': r'<(article|div|section)\s+class\s*=\s*["\'][^"\']*\bcard\b[^"\']*["\']',
            'button': r'<(button|a)\s+class\s*=\s*["\'][^"\']*\bbtn\b[^"\']*["\']',
            'input': r'<input\s+class\s*=\s*["\'][^"\']*\binput\b[^"\']*["\']',
            'badge': r'<(span|div)\s+class\s*=\s*["\'][^"\']*\bbadge\b[^"\']*["\']',
            'avatar': r'<(img|div)\s+class\s*=\s*["\'][^"\']*\bavatar\b[^"\']*["\']',
            'modal': r'<(div|dialog)\s+class\s*=\s*["\'][^"\']*\bmodal\b[^"\']*["\']',
            'dropdown': r'<(div|select)\s+class\s*=\s*["\'][^"\']*\bdropdown\b[^"\']*["\']',
            'table': r'<table\s+class\s*=\s*["\'][^"\']*["\']',
            'form': r'<form\s+class\s*=\s*["\'][^"\']*["\']',
            'nav': r'<nav\s+class\s*=\s*["\'][^"\']*["\']',
            'header': r'<header\s+class\s*=\s*["\'][^"\']*["\']',
            'footer': r'<footer\s+class\s*=\s*["\'][^"\']*["\']',
            'sidebar': r'<(aside|div)\s+class\s*=\s*["\'][^"\']*\bsidebar\b[^"\']*["\']',
        }
        
        for comp_name, pattern in component_patterns.items():
            matches = list(re.finditer(pattern, html_content, re.IGNORECASE))
            if matches:
                for match in matches[:3]:  # Limit to first 3 examples
                    start = match.start()
                    end_tag = self._find_closing_tag(html_content, start, 'div')
                    if end_tag:
                        html_template = html_content[start:end_tag]
                        
                        component = Component(
                            name=comp_name,
                            selector=f".{comp_name}",
                            tokens=[],
                            variants={},
                            states={},
                            html_template=html_template[:2000],
                            description=f"Auto-extracted {comp_name} component",
                            file_path=file_path
                        )
                        components.append(component)
                        self.components[comp_name] = component
        
        return components
    
    def _find_closing_tag(self, html: str, start: int, tag_name: str) -> Optional[int]:
        """Find matching closing tag (simplified)"""
        open_count = 1
        pos = start
        tag_pattern = re.compile(f'<(/?){re.escape(tag_name)}\\b', re.IGNORECASE)
        
        for match in tag_pattern.finditer(html[start+1:]):
            if match.group(1) == '/':  # Closing tag
                open_count -= 1
                if open_count == 0:
                    return start + 1 + match.end()
            else:  # Opening tag
                open_count += 1
        
        return None
    
    def extract_from_css(self, css_content: str, file_path: str = "") -> List[Component]:
        """Extract component patterns from CSS (BEM, utility components, etc.)"""
        components = []
        
        # BEM pattern: .block__element--modifier
        bem_pattern = re.compile(r'\.(\w+)__(?:[\w-]+)(?:--[\w-]+)?\s*\{')
        bem_blocks = set()
        
        for match in bem_pattern.finditer(css_content):
            block = match.group(1)
            bem_blocks.add(block)
        
        for block in bem_blocks:
            component = Component(
                name=block,
                selector=f".{block}",
                tokens=[],
                variants={},
                states={},
                description=f"BEM block: {block}",
                file_path=file_path
            )
            components.append(component)
            self.components[block] = component
        
        # Component-like class patterns: .c-button, .c-card, .component-name
        comp_pattern = re.compile(r'\.(?:c-|component-)(\w+(?:-\w+)*)\s*\{')
        for match in comp_pattern.finditer(css_content):
            name = match.group(1)
            if name not in self.components:
                component = Component(
                    name=name,
                    selector=f".c-{name}",
                    tokens=[],
                    variants={},
                    states={},
                    description=f"Component class: c-{name}",
                    file_path=file_path
                )
                components.append(component)
                self.components[name] = component
        
        return components
    
    def map_tokens_to_components(self, design_system: DesignSystem):
        """Map extracted tokens to components based on usage"""
        for comp in design_system.components.values():
            comp_tokens = []
            for token_name, token in design_system.tokens.items():
                if comp.selector and token.value in comp.selector:
                    comp_tokens.append(token_name)
                if comp.html_template and token.value in comp.html_template:
                    comp_tokens.append(token_name)
            
            comp.tokens = list(set(comp_tokens))


class DesignSystemEngine:
    """Main engine orchestrating extraction and management"""
    
    def __init__(self, workspace_root: str = "."):
        self.workspace_root = Path(workspace_root).resolve()
        self.token_extractor = TokenExtractor()
        self.component_extractor = ComponentExtractor(self.token_extractor)
        self.design_system = DesignSystem()
    
    def scan_workspace(self, extensions: List[str] = None) -> DesignSystem:
        """Scan workspace for design system artifacts"""
        if extensions is None:
            extensions = ['.css', '.scss', '.sass', '.less', '.html', '.htm', '.js', '.ts', '.jsx', '.tsx', '.vue', '.svelte', '.json']
        
        files_scanned = 0
        
        for ext in extensions:
            for file_path in self.workspace_root.rglob(f'*{ext}'):
                if self._should_skip(file_path):
                    continue
                
                try:
                    content = file_path.read_text(encoding='utf-8')
                    rel_path = str(file_path.relative_to(self.workspace_root))
                    
                    if ext in ['.css', '.scss', '.sass', '.less']:
                        self.token_extractor.extract_from_css(content, rel_path)
                        self.token_extractor.extract_from_css_variables(content, rel_path)
                        self.component_extractor.extract_from_css(content, rel_path)
                    
                    elif ext in ['.html', '.htm']:
                        self.token_extractor.extract_from_html(content, rel_path)
                        self.component_extractor.extract_from_html(content, rel_path)
                    
                    elif ext in ['.js', '.ts', '.jsx', '.tsx', '.vue', '.svelte']:
                        self.token_extractor.extract_from_js(content, rel_path)
                    
                    elif ext == '.json' and 'figma' in file_path.name.lower():
                        try:
                            figma_data = json.loads(content)
                            self.token_extractor.extract_from_figma(figma_data, rel_path)
                        except:
                            pass
                    
                    files_scanned += 1
                    
                except Exception as e:
                    print(f"Error scanning {file_path}: {e}")
        
        # Consolidate
        self.design_system.tokens = self.token_extractor.tokens
        self.design_system.components = self.component_extractor.components
        self.design_system.metadata = {
            'workspace_root': str(self.workspace_root),
            'files_scanned': files_scanned,
            'token_count': len(self.design_system.tokens),
            'component_count': len(self.design_system.components),
            'extracted_at': self._now_iso()
        }
        
        # Map tokens to components
        self.component_extractor.map_tokens_to_components(self.design_system)
        
        return self.design_system
    
    def _should_skip(self, path: Path) -> bool:
        """Check if path should be skipped"""
        skip_dirs = {'node_modules', '.git', '__pycache__', '.venv', 'venv', 'dist', 'build', '.next', '.cache', 'target'}
        return any(part in skip_dirs for part in path.parts)
    
    def _now_iso(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    
    def save(self, output_path: str):
        """Save design system to JSON"""
        Path(output_path).write_text(self.design_system.to_json(), encoding='utf-8')
    
    def load(self, input_path: str) -> DesignSystem:
        """Load design system from JSON"""
        data = json.loads(Path(input_path).read_text(encoding='utf-8'))
        
        self.design_system.tokens = {
            k: DesignToken(**v) for k, v in data.get('tokens', {}).items()
        }
        self.design_system.components = {
            k: Component(**v) for k, v in data.get('components', {}).items()
        }
        self.design_system.patterns = data.get('patterns', {})
        self.design_system.metadata = data.get('metadata', {})
        
        # Sync extractors
        self.token_extractor.tokens = self.design_system.tokens
        self.component_extractor.components = self.design_system.components
        
        return self.design_system
    
    def get_tokens_by_type(self, token_type: str) -> Dict[str, DesignToken]:
        """Filter tokens by type"""
        return {k: v for k, v in self.design_system.tokens.items() if v.type == token_type}
    
    def get_color_palette(self) -> Dict[str, str]:
        """Get color tokens as palette"""
        colors = self.get_tokens_by_type('color')
        return {k: v.value for k, v in colors.items()}
    
    def get_spacing_scale(self) -> Dict[str, str]:
        """Get spacing tokens as scale"""
        spacing = self.get_tokens_by_type('spacing')
        return {k: v.value for k, v in spacing.items()}
    
    def get_typography_scale(self) -> Dict[str, str]:
        """Get typography tokens"""
        typo = self.get_tokens_by_type('typography')
        return {k: v.value for k, v in typo.items()}
    
    def generate_css_variables(self, prefix: str = "hds") -> str:
        """Generate CSS custom properties from tokens"""
        lines = [":root {"]
        
        for token in self.design_system.tokens.values():
            css_name = f"--{prefix}-{token.name}"
            lines.append(f"  {css_name}: {token.value};")
        
        lines.append("}")
        return "\n".join(lines)
    
    def generate_tailwind_config(self) -> Dict[str, Any]:
        """Generate Tailwind config extension from tokens"""
        config = {
            "theme": {
                "extend": {
                    "colors": {},
                    "spacing": {},
                    "fontSize": {},
                    "fontFamily": {},
                    "borderRadius": {},
                    "boxShadow": {},
                    "zIndex": {},
                    "opacity": {}
                }
            }
        }
        
        for token in self.design_system.tokens.values():
            if token.type == 'color':
                config["theme"]["extend"]["colors"][token.name] = token.value
            elif token.type == 'spacing':
                config["theme"]["extend"]["spacing"][token.name] = token.value
            elif token.type == 'typography':
                if 'font' in token.name:
                    config["theme"]["extend"]["fontFamily"][token.name] = token.value
                else:
                    config["theme"]["extend"]["fontSize"][token.name] = token.value
            elif token.type == 'radius':
                config["theme"]["extend"]["borderRadius"][token.name] = token.value
            elif token.type == 'shadow':
                config["theme"]["extend"]["boxShadow"][token.name] = token.value
            elif token.type == 'z-index':
                config["theme"]["extend"]["zIndex"][token.name] = token.value
            elif token.type == 'opacity':
                config["theme"]["extend"]["opacity"][token.name] = token.value
        
        return config


# CLI entry point
if __name__ == "__main__":
    import sys
    
    workspace = sys.argv[1] if len(sys.argv) > 1 else "."
    output = sys.argv[2] if len(sys.argv) > 2 else "design-system.json"
    
    engine = DesignSystemEngine(workspace)
    ds = engine.scan_workspace()
    engine.save(output)
    
    print(f"Scanned workspace: {workspace}")
    print(f"Tokens extracted: {len(ds.tokens)}")
    print(f"Components found: {len(ds.components)}")
    print(f"Saved to: {output}")
    
    # Print summary by type
    by_type = defaultdict(int)
    for token in ds.tokens.values():
        by_type[token.type] += 1
    
    print("\nTokens by type:")
    for t, count in sorted(by_type.items()):
        print(f"  {t}: {count}")