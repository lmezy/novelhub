// NovelHub jsoup shim for Legado (YueDu) rule JS.
// Provides a faithful-enough subset of org.jsoup + java.* + Java String
// semantics so that exported YueDu sources using <js> rules work in Node.js.

// ---------------- Java-style regex helpers ----------------
function __nhJavaFlags(pattern) {
  var flags = '';
  var m = /^\\(\\?([a-zA-Z]+)\\)/.exec(pattern);
  if (m) {
    var f = m[1];
    if (f.indexOf('i') >= 0) flags += 'i';
    if (f.indexOf('s') >= 0) flags += 's';
    if (f.indexOf('m') >= 0) flags += 'm';
    pattern = pattern.slice(m[0].length);
  }
  return { pattern: pattern, flags: flags };
}
function __nhJavaToJs(pattern, global) {
  var jf = __nhJavaFlags(String(pattern));
  var p = jf.pattern
    .replace(/\\Q([\s\S]*?)\\E/g, function (_, s) {
      return s.replace(/[.*+?^()|[\]{}]{}$\\]/g, '\\$&');
    })
    .replace(/(\\.)\+\+/g, '$1')
    .replace(/\(\?>([\s\S]*?)\)/g, '(?:$1)')
    .replace(/\\z/g, '$').replace(/\\A/g, '^').replace(/\\Z/g, '$');
  try {
    return new RegExp(p, jf.flags + (global ? 'g' : ''));
  } catch (e) {
    return null;
  }
}
function __nhNativeReplaceAll(s, pat, repl) {
  if (pat instanceof RegExp) {
    var g = pat.global ? pat : new RegExp(pat.source, pat.flags.indexOf('g') >= 0 ? pat.flags : pat.flags + 'g');
    return s.replace(g, repl);
  }
  var rx = __nhJavaToJs(pat, true);
  if (rx) return s.replace(rx, repl);
  return s.split(pat).join(repl);
}
if (typeof String.prototype.replaceAll !== 'function' || String.prototype.replaceAll.__nh) {
  String.prototype.replaceAll = function (pat, repl) {
    return __nhNativeReplaceAll(String(this), pat, repl);
  };
  String.prototype.replaceAll.__nh = true;
} else {
  var __nhOrigReplaceAll = String.prototype.replaceAll;
  String.prototype.replaceAll = function (pat, repl) {
    if (typeof pat === 'string') return __nhNativeReplaceAll(String(this), pat, repl);
    return __nhOrigReplaceAll.call(this, pat, repl);
  };
  String.prototype.replaceAll.__nh = true;
}
String.prototype.replaceFirst = String.prototype.replaceFirst || function (pat, repl) {
  var s = String(this);
  if (pat instanceof RegExp) return s.replace(pat, repl);
  var rx = __nhJavaToJs(pat, false);
  if (rx) return s.replace(rx, repl);
  return s.replace(pat, repl);
};
String.prototype.matches = String.prototype.matches || function (pat) {
  var s = String(this);
  var rx = __nhJavaToJs(pat, false);
  if (!rx) return false;
  var m = s.match(rx);
  return !!m && m[0] === s;
};
var __nhOrigSplit = String.prototype.split;
String.prototype.split = function (sep, limit) {
  var s = String(this);
  if (typeof sep === 'string' && /[\\^$.*+?()[\]{}|]/.test(sep)) {
    var rx = __nhJavaToJs(sep, false);
    if (rx) return s.split(rx, limit);
  }
  return __nhOrigSplit.call(s, sep, limit);
};

// ---------------- Minimal HTML parser ----------------
var __nhVoidTags = {
  area:1, base:1, br:1, col:1, embed:1, hr:1, img:1, input:1,
  link:1, meta:1, param:1, source:1, track:1, wbr:1,
};
function __nhParseAttrs(str) {
  var attrs = {};
  var re = /([a-zA-Z_:][a-zA-Z0-9_:.-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>]+)))?/g;
  var m;
  while ((m = re.exec(str))) {
    attrs[m[1].toLowerCase()] = m[2] !== undefined ? m[2] : (m[3] !== undefined ? m[3] : (m[4] !== undefined ? m[4] : ''));
  }
  return attrs;
}
var __nhSeq = 1;
function __nhEl(tag, attrs, isText, data) {
  this.__nhId = __nhSeq++;
  this.tag = tag || '';
  this.attrs = attrs || {};
  this._children = [];
  this._parent = null;
  this.isText = !!isText;
  this.data = data != null ? data : '';
  this.rawText = '';
}
__nhEl.prototype._isEl = true;
function __nhAddText(stack, text) {
  if (!text) return;
  var parent = stack[stack.length - 1];
  parent._children.push(new __nhEl('#text', {}, true, text));
}
function __nhParse(html) {
  var root = new __nhEl('#root', {});
  var stack = [root];
  var pos = 0;
  var len = html.length;
  while (pos < len) {
    var lt = html.indexOf('<', pos);
    if (lt === -1) { __nhAddText(stack, html.slice(pos)); break; }
    if (lt > pos) __nhAddText(stack, html.slice(pos, lt));
    if (html.startsWith('<!--', lt)) {
      var ce = html.indexOf('-->', lt + 4);
      pos = ce === -1 ? len : ce + 3;
      continue;
    }
    if (html.startsWith('<!', lt) || html.startsWith('<?', lt)) {
      var de = html.indexOf('>', lt);
      pos = de === -1 ? len : de + 1;
      continue;
    }
    var gt = html.indexOf('>', lt);
    if (gt === -1) { __nhAddText(stack, html.slice(lt)); break; }
    var tagStr = html.slice(lt + 1, gt);
    if (/^\s*\//.test(tagStr)) {
      var closeName = (tagStr.match(/^\s*\/\s*([a-zA-Z][a-zA-Z0-9]*)/) || [])[1];
      if (closeName) {
        var open = closeName.toLowerCase();
        for (var i = stack.length - 1; i > 0; i--) {
          if (stack[i].tag === open) { stack = stack.slice(0, i); break; }
        }
      }
      pos = gt + 1;
      continue;
    }
    var m = tagStr.match(/^\s*([a-zA-Z][a-zA-Z0-9]*)/);
    if (!m) { pos = gt + 1; continue; }
    var tagName = m[1].toLowerCase();
    var selfClose = /\/\s*$/.test(tagStr);
    var el = new __nhEl(tagName, __nhParseAttrs(tagStr.slice(m[0].length)));
    var parent = stack[stack.length - 1];
    el._parent = parent;
    parent._children.push(el);
    if (tagName === 'script' || tagName === 'style') {
      var closeRe = new RegExp('</' + tagName + '\\s*>', 'i');
      var cm = closeRe.exec(html.slice(gt + 1));
      if (cm) {
        el.rawText = html.slice(gt + 1, gt + 1 + cm.index);
        pos = gt + 1 + cm.index + cm[0].length;
      } else { pos = len; }
      continue;
    }
    if (selfClose || __nhVoidTags[tagName]) { pos = gt + 1; continue; }
    stack.push(el);
    pos = gt + 1;
  }
  return root;
}

// ---------------- Elements collection (jsoup Elements) ----------------
function __nhElements(arr) {
  var els = arr || [];
  els.size = function () { return els.length; };
  els.get = function (i) { return els[i]; };
  els.first = function () { return els.length ? els[0] : null; };
  els.last = function () { return els.length ? els[els.length - 1] : null; };
  els.isEmpty = function () { return els.length === 0; };
  els.eachAttr = function (name) {
    var out = [];
    for (var i = 0; i < els.length; i++) out.push(els[i].attr(name));
    return out;
  };
  els.eachText = function () {
    var out = [];
    for (var i = 0; i < els.length; i++) out.push(els[i].text());
    return out;
  };
  els.select = function (css) {
    var out = [];
    for (var i = 0; i < els.length; i++) {
      var sub = els[i].select(css);
      for (var j = 0; j < sub.length; j++) out.push(sub[j]);
    }
    return __nhElements(out);
  };
  els.remove = function () { for (var i = els.length - 1; i >= 0; i--) els[i].remove(); return els; };
  els.addClass = function (c) { for (var i = 0; i < els.length; i++) els[i].addClass(c); return els; };
  els.removeClass = function (c) { for (var i = 0; i < els.length; i++) els[i].removeClass(c); return els; };
  els.hasClass = function (c) { for (var i = 0; i < els.length; i++) if (els[i].hasClass(c)) return true; return false; };
  els.text = function () { var t = []; for (var i = 0; i < els.length; i++) t.push(els[i].text()); return t.join(' '); };
  els.html = function () { var t = []; for (var i = 0; i < els.length; i++) t.push(els[i].html()); return t.join(''); };
  els.outerHtml = function () { var t = []; for (var i = 0; i < els.length; i++) t.push(els[i].outerHtml()); return t.join(''); };
  els.toString = function () { return els.outerHtml(); };
  els.toArray = function () { return els.slice(); };
  return els;
}

// ---------------- CSS selector engine (subset) ----------------
function __nhTokenizeSimple(sel) {
  var tokens = [];
  var re = /(#[\w-]+|\.(?:[\w-]|[\u00a0-\uffff])+|\[[^\]]*\]|:[a-zA-Z-]+(?:\([^)]*\))?|[a-zA-Z][a-zA-Z0-9]*)/g;
  var m;
  while ((m = re.exec(sel))) tokens.push(m[0]);
  return tokens;
}
function __nhSimpleMatch(el, tokens) {
  if (!el || el.isText) return false;
  for (var i = 0; i < tokens.length; i++) {
    var t = tokens[i];
    if (t[0] === '#') {
      if (el.attrs.id !== t.slice(1)) return false;
    } else if (t[0] === '.') {
      var cls = t.slice(1);
      var classes = (el.attrs['class'] || '').split(/\s+/);
      if (classes.indexOf(cls) === -1) return false;
    } else if (t[0] === '[') {
      var inner = t.slice(1, -1).trim();
      var am = inner.match(/^([\w-]+)(?:\s*(~|\^|\$|\*|)=?\s*["']?([^"']*)["']?)?$/);
      if (!am) return false;
      var name = am[1].toLowerCase();
      var op = am[2] || '';
      var val = am[3] !== undefined ? am[3] : '';
      var attrVal = el.attrs[name];
      if (attrVal === undefined) return false;
      if (!op) return true;
      if (op === '=') return attrVal === val;
      if (op === '~') return (' ' + attrVal + ' ').indexOf(' ' + val + ' ') >= 0;
      if (op === '^') return attrVal.indexOf(val) === 0;
      if (op === '$') return attrVal.length >= val.length && attrVal.slice(attrVal.length - val.length) === val;
      if (op === '*') return attrVal.indexOf(val) >= 0;
      return false;
    } else if (t[0] === ':') {
      var pm = t.match(/^:([a-zA-Z-]+)(?:\(([^)]*)\))?$/);
      if (!pm) return false;
      var pname = pm[1];
      var parg = pm[2] !== undefined ? pm[2] : '';
      if (pname === 'first-child') {
        if (el.previousElementSibling()) return false;
      } else if (pname === 'last-child') {
        if (el.nextElementSibling()) return false;
      } else if (pname === 'only-child') {
        if (el.previousElementSibling() || el.nextElementSibling()) return false;
      } else if (pname === 'nth-child') {
        var idx = el.elementIndex() + 1;
        var spec = parg.trim();
        var nm = spec.match(/^(\d+)$|^(?:even|odd)$|^([+-]?\d*)n(?:\s*([+-])\s*(\d+))?$/);
        if (!nm) return false;
        var ok = false;
        if (nm[1]) ok = (idx === parseInt(nm[1], 10));
        else if (nm[2] === 'even') ok = (idx % 2 === 0);
        else if (nm[2] === 'odd') ok = (idx % 2 === 1);
        else {
          var a = nm[3] === '' || nm[3] === undefined ? 1 : (nm[3] === '-' ? -1 : parseInt(nm[3], 10));
          var b = nm[4] ? (nm[5] === '-' ? -parseInt(nm[4], 10) : parseInt(nm[4], 10)) : 0;
          var k = (idx - b) / a;
          ok = Number.isInteger(k) && k >= 0;
        }
        if (!ok) return false;
      } else if (pname === 'contains') {
        var needle = parg.trim().replace(/^["']|["']$/g, '');
        if (el.text().indexOf(needle) === -1) return false;
      } else if (pname === 'has') {
        if (!el.select(parg).length) return false;
      } else if (pname === 'not') {
        if (el.matches(parg)) return false;
      } else if (pname === 'empty') {
        if (el._children.length > 0 && !(el._children.length === 1 && el._children[0].isText && el._children[0].data === '')) return false;
      } else if (pname === 'containsOwn') {
        var needle2 = parg.trim().replace(/^["']|["']$/g, '');
        if (el.ownText().indexOf(needle2) === -1) return false;
      }
    } else {
      if (el.tag.toLowerCase() !== t.toLowerCase()) return false;
    }
  }
  return true;
}
function __nhParseCompound(sel) {
  var parts = [];
  var buf = '';
  var combo = null;
  for (var i = 0; i < sel.length; i++) {
    var c = sel[i];
    if (c === '>' || c === '+' || c === '~') {
      var prev = buf.trim();
      if (prev) { parts.push({ combo: combo, sel: prev }); combo = null; buf = ''; }
      combo = c;
      while (sel[i + 1] === ' ') i++;
      continue;
    }
    if (c === ' ' && buf.trim()) {
      parts.push({ combo: combo, sel: buf.trim() });
      combo = ' ';
      buf = '';
      while (sel[i + 1] === ' ') i++;
      continue;
    }
    buf += c;
  }
  var last = buf.trim();
  if (last) parts.push({ combo: combo, sel: last });
  return parts;
}
__nhEl.prototype.elementIndex = function () {
  var idx = 0;
  var sib = this.previousElementSibling();
  while (sib) { idx++; sib = sib.previousElementSibling(); }
  return idx;
};
__nhEl.prototype.matches = function (css) {
  var groups = String(css).split(',').map(function (s) { return s.trim(); }).filter(Boolean);
  for (var gi = 0; gi < groups.length; gi++) {
    var parts = __nhParseCompound(groups[gi]);
    if (parts.length === 1 && __nhSimpleMatch(this, __nhTokenizeSimple(parts[0].sel))) return true;
  }
  return false;
};
__nhEl.prototype.select = function (css) {
  if (!css || typeof css !== 'string') return __nhElements([]);
  var groups = css.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
  var out = [];
  var seen = {};
  var self = this;
  function walk(el) {
    for (var i = 0; i < el._children.length; i++) {
      var child = el._children[i];
      if (!child.isText) {
        if (__nhCompoundMatch(child, parts) && !seen[child.__nhId]) {
          seen[child.__nhId] = 1;
          out.push(child);
        }
        walk(child);
      }
    }
  }
  for (var gi = 0; gi < groups.length; gi++) {
    var parts = __nhParseCompound(groups[gi]);
    if (!parts.length) continue;
    walk(self);
  }
  return __nhElements(out);
};
function __nhCompoundMatch(el, parts) {
  var right = parts[parts.length - 1];
  if (!__nhSimpleMatch(el, __nhTokenizeSimple(right.sel))) return false;
  for (var i = parts.length - 2; i >= 0; i--) {
    var part = parts[i];
    var combo = parts[i + 1].combo;
    if (combo === '>') {
      el = el._parent;
      if (!el || el.isText || !__nhSimpleMatch(el, __nhTokenizeSimple(part.sel))) return false;
    } else if (combo === '+') {
      var prev = el.previousElementSibling();
      if (!prev || !__nhSimpleMatch(prev, __nhTokenizeSimple(part.sel))) return false;
      el = prev;
    } else if (combo === '~') {
      var found = false;
      var sib = el.previousElementSibling();
      while (sib) {
        if (__nhSimpleMatch(sib, __nhTokenizeSimple(part.sel))) { found = true; break; }
        sib = sib.previousElementSibling();
      }
      if (!found) return false;
      el = sib;
    } else {
      var anc = el._parent;
      var matched = false;
      while (anc && !anc.isText) {
        if (__nhSimpleMatch(anc, __nhTokenizeSimple(part.sel))) { matched = true; break; }
        anc = anc._parent;
      }
      if (!matched) return false;
      el = anc;
    }
  }
  return true;
}

// ---------------- Element API (jsoup-style) ----------------
__nhEl.prototype.attr = function (name, value) {
  if (arguments.length >= 2) {
    this.attrs[String(name).toLowerCase()] = String(value);
    return this;
  }
  var v = this.attrs[String(name).toLowerCase()];
  return v === undefined ? '' : v;
};
__nhEl.prototype.hasAttr = function (name) { return this.attrs[String(name).toLowerCase()] !== undefined; };
__nhEl.prototype.removeAttr = function (name) { delete this.attrs[String(name).toLowerCase()]; return this; };
__nhEl.prototype.absUrl = function (name) {
  var v = this.attr(name);
  if (!v) return '';
  try { return new URL(v, this._baseUrl || '').href; } catch (e) { return v; }
};
__nhEl.prototype.attributes = function () {
  var out = {};
  for (var k in this.attrs) out[k] = this.attrs[k];
  return out;
};
__nhEl.prototype.text = function () {
  var parts = [];
  function collect(el) {
    if (el.isText) { parts.push(el.data); return; }
    for (var i = 0; i < el._children.length; i++) collect(el._children[i]);
  }
  collect(this);
  return parts.join('').replace(/\s+/g, ' ').trim();
};
__nhEl.prototype.ownText = function () {
  var parts = [];
  for (var i = 0; i < this._children.length; i++) {
    if (this._children[i].isText) parts.push(this._children[i].data);
  }
  return parts.join('').replace(/\s+/g, ' ').trim();
};
__nhEl.prototype.ownTexts = function () {
  var parts = [];
  for (var i = 0; i < this._children.length; i++) {
    if (this._children[i].isText) {
      var t = this._children[i].data.replace(/\s+/g, ' ').trim();
      if (t) parts.push(t);
    }
  }
  return parts;
};
__nhEl.prototype.textNodes = function () {
  var out = [];
  for (var i = 0; i < this._children.length; i++) {
    if (this._children[i].isText) out.push(this._children[i]);
  }
  return out;
};
__nhEl.prototype.ownTextNodes = function () { return this.textNodes(); };
__nhEl.prototype.data = function () {
  if (this.rawText) return this.rawText;
  var t = '';
  for (var i = 0; i < this._children.length; i++) {
    if (this._children[i].isText) t += this._children[i].data;
  }
  return t;
};
__nhEl.prototype.html = function (value) {
  if (arguments.length) {
    this.children = [];
    var frag = __nhParse(String(value));
    for (var i = 0; i < frag._children.length; i++) {
      frag._children[i].parent = this;
      this._children.push(frag._children[i]);
    }
    return this;
  }
  var out = '';
  for (var i = 0; i < this._children.length; i++) out += __nhSerialize(this._children[i]);
  return out;
};
__nhEl.prototype.outerHtml = function () { return __nhSerialize(this); };
__nhEl.prototype.toString = function () { return this.outerHtml(); };
function __nhSerialize(el) {
  if (el.isText) return el.data;
  var attrs = '';
  for (var k in el.attrs) {
    var v = el.attrs[k];
    attrs += ' ' + k + (v !== '' ? '="' + String(v).replace(/"/g, '&quot;') + '"' : '');
  }
  var open = '<' + el.tag + attrs + '>';
  if (el.tag === 'script' || el.tag === 'style') return open + el.rawText + '</' + el.tag + '>';
  if (__nhVoidTags[el.tag]) return open;
  var inner = '';
  for (var i = 0; i < el._children.length; i++) inner += __nhSerialize(el._children[i]);
  return open + inner + '</' + el.tag + '>';
}
__nhEl.prototype.parent = function () { return this._parent || null; };
__nhEl.prototype.children = function () { return __nhElements(this._children.filter(function (c) { return !c.isText; })); };
__nhEl.prototype.childNodeSize = function () { return this._children.length; };
__nhEl.prototype.firstElementChild = function () {
  for (var i = 0; i < this._children.length; i++) if (!this._children[i].isText) return this._children[i];
  return null;
};
__nhEl.prototype.lastElementChild = function () {
  for (var i = this._children.length - 1; i >= 0; i--) if (!this._children[i].isText) return this._children[i];
  return null;
};
__nhEl.prototype.nextElementSibling = function () {
  var p = this._parent;
  if (!p) return null;
  var found = false;
  for (var i = 0; i < p._children.length; i++) {
    if (p._children[i] === this) { found = true; continue; }
    if (found && !p._children[i].isText) return p._children[i];
  }
  return null;
};
__nhEl.prototype.previousElementSibling = function () {
  var p = this._parent;
  if (!p) return null;
  for (var i = p._children.length - 1; i >= 0; i--) {
    if (p._children[i] === this) break;
    if (!p._children[i].isText) return p._children[i];
  }
  return null;
};
__nhEl.prototype.siblingElements = function () {
  var out = [];
  var p = this._parent;
  if (!p) return __nhElements(out);
  for (var i = 0; i < p._children.length; i++) {
    var c = p._children[i];
    if (c !== this && !c.isText) out.push(c);
  }
  return __nhElements(out);
};
__nhEl.prototype.addClass = function (c) {
  var classes = (this.attrs['class'] || '').split(/\s+/).filter(Boolean);
  if (classes.indexOf(c) === -1) classes.push(c);
  this.attrs['class'] = classes.join(' ');
  return this;
};
__nhEl.prototype.removeClass = function (c) {
  var classes = (this.attrs['class'] || '').split(/\s+/).filter(function (x) { return x && x !== c; });
  this.attrs['class'] = classes.join(' ');
  return this;
};
__nhEl.prototype.hasClass = function (c) {
  return (' ' + (this.attrs['class'] || '') + ' ').indexOf(' ' + c + ' ') >= 0;
};
__nhEl.prototype.className = function () { return this.attrs['class'] || ''; };
__nhEl.prototype.classNames = function () { return (this.attrs['class'] || '').split(/\s+/).filter(Boolean); };
__nhEl.prototype.id = function () { return this.attrs.id || ''; };
__nhEl.prototype.tagName = function () { return this.tag; };
__nhEl.prototype.nodeName = function () { return this.tag; };
__nhEl.prototype.val = function () { return this.attrs.value || ''; };
__nhEl.prototype.remove = function () {
  var p = this._parent;
  if (!p) return this;
  for (var i = 0; i < p._children.length; i++) {
    if (p._children[i] === this) { p._children.splice(i, 1); break; }
  }
  this._parent = null;
  return this;
};
__nhEl.prototype.append = function (htmlOrEl) {
  if (htmlOrEl && htmlOrEl._isEl) {
    htmlOrEl.parent = this;
    this._children.push(htmlOrEl);
  } else {
    var frag = __nhParse(String(htmlOrEl));
    for (var i = 0; i < frag._children.length; i++) {
      frag._children[i].parent = this;
      this._children.push(frag._children[i]);
    }
  }
  return this;
};
__nhEl.prototype.appendChild = function (el) { return this.append(el); };
__nhEl.prototype.prepend = function (htmlOrEl) {
  if (htmlOrEl && htmlOrEl._isEl) {
    htmlOrEl.parent = this;
    this._children.unshift(htmlOrEl);
  } else {
    var frag = __nhParse(String(htmlOrEl));
    var nodes = frag._children.slice();
    for (var i = nodes.length - 1; i >= 0; i--) {
      nodes[i].parent = this;
      this._children.unshift(nodes[i]);
    }
  }
  return this;
};
__nhEl.prototype.getElementsByTag = function (tag) {
  var out = [];
  var self = this;
  function walk(el) {
    for (var i = 0; i < el._children.length; i++) {
      var c = el._children[i];
      if (!c.isText) {
        if (c.tag.toLowerCase() === String(tag).toLowerCase()) out.push(c);
        walk(c);
      }
    }
  }
  walk(self);
  return __nhElements(out);
};
__nhEl.prototype.getElementsByClass = function (cls) {
  var out = [];
  var self = this;
  function walk(el) {
    for (var i = 0; i < el._children.length; i++) {
      var c = el._children[i];
      if (!c.isText) {
        if (c.hasClass(cls)) out.push(c);
        walk(c);
      }
    }
  }
  walk(self);
  return __nhElements(out);
};
__nhEl.prototype.getElementById = function (id) {
  var found = null;
  var self = this;
  function walk(el) {
    if (found) return;
    for (var i = 0; i < el._children.length; i++) {
      var c = el._children[i];
      if (!c.isText) {
        if (c.attrs.id === id) { found = c; return; }
        walk(c);
      }
    }
  }
  walk(self);
  return found;
};
__nhEl.prototype.getElementsByAttribute = function (name) {
  var out = [];
  var self = this;
  function walk(el) {
    for (var i = 0; i < el._children.length; i++) {
      var c = el._children[i];
      if (!c.isText) {
        if (c.attrs[String(name).toLowerCase()] !== undefined) out.push(c);
        walk(c);
      }
    }
  }
  walk(self);
  return __nhElements(out);
};
__nhEl.prototype.getElementsContainingText = function (text) {
  var out = [];
  var self = this;
  function walk(el) {
    for (var i = 0; i < el._children.length; i++) {
      var c = el._children[i];
      if (!c.isText) {
        if (c.text().indexOf(text) >= 0) out.push(c);
        walk(c);
      }
    }
  }
  walk(self);
  return __nhElements(out);
};
__nhEl.prototype.toJSON = function () {
  var out = { tag: this.tag };
  if (this.attrs && Object.keys(this.attrs).length) out.attrs = this.attrs;
  for (var k in this.attrs) out[k] = this.attrs[k];
  out.text = this.ownText() || this.text();
  out.html = this.html();
  return out;
};

// ---------------- Document / Jsoup ----------------
function __nhDocument(html) {
  __nhEl.call(this, '#document', {});
  this._root = __nhParse(String(html));
  this._root._parent = this;
  this._children = this._root._children;
  this._baseUrl = '';
}
__nhDocument.prototype = Object.create(__nhEl.prototype);
__nhDocument.prototype.constructor = __nhDocument;
__nhDocument.prototype.select = function (css) { return this._root.select(css); };
__nhDocument.prototype.body = function () {
  var b = this._root.getElementsByTag('body').first();
  if (!b) {
    b = new __nhEl('body', {});
    this._root._children.push(b);
    b._parent = this._root;
  }
  return b;
};
__nhDocument.prototype.head = function () {
  var h = this._root.getElementsByTag('head').first();
  if (!h) {
    h = new __nhEl('head', {});
    this._root._children.unshift(h);
    h._parent = this._root;
  }
  return h;
};
__nhDocument.prototype.title = function () {
  var t = this._root.getElementsByTag('title').first();
  return t ? t.text() : '';
};
__nhDocument.prototype.createTextNode = function (data) { return new __nhEl('#text', {}, true, String(data)); };
__nhDocument.prototype.createElement = function (tag) { return new __nhEl(String(tag).toLowerCase(), {}); };
__nhDocument.prototype.outerHtml = function () {
  var out = '';
  for (var i = 0; i < this._root._children.length; i++) out += __nhSerialize(this._root._children[i]);
  return out;
};
__nhDocument.prototype.html = function () { return this.outerHtml(); };
__nhDocument.prototype.text = function () { return this._root.text(); };
__nhDocument.prototype.ownText = function () { return this._root.ownText(); };
__nhDocument.prototype.getElementsByTag = function (tag) { return this._root.getElementsByTag(tag); };
__nhDocument.prototype.getElementsByClass = function (c) { return this._root.getElementsByClass(c); };
__nhDocument.prototype.getElementById = function (id) { return this._root.getElementById(id); };
__nhDocument.prototype.parent = function () { return null; };

function __nhConnection(url) {
  this.url = url;
  this.headers = {};
  this.cookies = {};
  this.ignoreContentTypeFlag = false;
  this.timeoutMs = 30000;
  this.methodName = 'GET';
  this.dataObj = {};
}
__nhConnection.prototype.ignoreContentType = function (v) { this.ignoreContentTypeFlag = !!v; return this; };
__nhConnection.prototype.header = function (k, v) { this.headers[k] = v; return this; };
__nhConnection.prototype.cookie = function (k, v) { this.cookies[k] = v; return this; };
__nhConnection.prototype.timeout = function (ms) { this.timeoutMs = parseInt(ms, 10) || 30000; return this; };
__nhConnection.prototype.method = function (m) { this.methodName = String(m).toUpperCase(); return this; };
__nhConnection.prototype.data = function (k, v) { this.dataObj[k] = v; return this; };
__nhConnection.prototype.userAgent = function (ua) { this.headers['User-Agent'] = ua; return this; };
__nhConnection.prototype.execute = function () {
  var headers = {};
  for (var k in this.headers) headers[k] = this.headers[k];
  if (Object.keys(this.cookies).length) {
    var cs = Object.keys(this.cookies).map(function (k) { return k + '=' + this.cookies[k]; }.bind(this)).join('; ');
    headers['Cookie'] = cs;
  }
  var body = '';
  if (this.methodName === 'POST' && Object.keys(this.dataObj).length) {
    var parts = [];
    for (var dk in this.dataObj) parts.push(encodeURIComponent(dk) + '=' + encodeURIComponent(this.dataObj[dk]));
    body = parts.join('&');
    headers['Content-Type'] = 'application/x-www-form-urlencoded';
  }
  var timeout = Math.max(5, Math.ceil(this.timeoutMs / 1000));
  var text = __nhCurlRaw(this.url, this.methodName, body || null, headers, timeout);
  if (text === null) text = '';
  return new __nhResponse(this.url, text, 200, {});
};

var org = {
  jsoup: {
    Jsoup: {
      parse: function (html) {
        return new __nhDocument(html);
      },
      connect: function (url) { return new __nhConnection(url); },
    },
  },
};

// ---------------- java / cookie / cache / Legado globals shims ----------------
var __nhCache = {};
var __nhCookieJar = [];
var __nhVars = {};
var __nhSourceConfig = {};
var __nhContent = '';
var __nhProxy = (typeof process !== 'undefined' && process.env && process.env.DSH_HTTP_PROXY) || '';

function __nhCurlRaw(url, method, body, headers, timeoutSec) {
  var execSync = require('child_process').execSync;
  var args = [];
  if (__nhProxy) {
    args.push("-x '" + String(__nhProxy).replace(/'/g, "'\\''") + "' -k");
  } else {
    args.push('-k');
  }
  if (headers) {
    for (var k in headers) {
      if (k == null) continue;
      var hv = String(headers[k]).replace(/'/g, "'\\''");
      args.push("-H '" + String(k) + ': ' + hv + "'");
    }
  }
  var t = parseInt(timeoutSec, 10) || 30;
  var cmd = 'curl -s -L --max-time ' + t +
    (method === 'POST' ? ' -X POST' : '') +
    (body ? " --data '" + String(body).replace(/'/g, "'\\''") + "'" : '') +
    (args.length ? ' ' + args.join(' ') : '') + ' "' + String(url).replace(/"/g, '\\"') + '"';
  try {
    return execSync(cmd, { maxBuffer: 64 * 1024 * 1024, timeout: (t + 10) * 1000 }).toString('utf-8');
  } catch (e) {
    return null;
  }
}

function __nhResponse(url, text, status, headers) {
  this._url = url || '';
  this._text = text == null ? '' : text;
  this._status = status || 200;
  this._headers = headers || {};
}
__nhResponse.prototype.body = function () { return this._text; };
__nhResponse.prototype.string = function () { return this._text; };
__nhResponse.prototype.url = function () { return this._url; };
__nhResponse.prototype.code = function () { return this._status; };
__nhResponse.prototype.statusCode = function () { return this._status; };
__nhResponse.prototype.isSuccess = function () { return this._status >= 200 && this._status < 300; };
__nhResponse.prototype.header = function (name) {
  if (!name) return null;
  var lower = String(name).toLowerCase();
  for (var k in this._headers) {
    if (String(k).toLowerCase() === lower) return this._headers[k];
  }
  return null;
};
__nhResponse.prototype.headers = function () { return this._headers; };
__nhResponse.prototype.json = function () { try { return JSON.parse(this._text); } catch (e) { return null; } };
__nhResponse.prototype.cookie = function () { return ''; };
__nhResponse.prototype.toString = function () { return this._text; };

function __nhCacheGet(k) {
  k = String(k);
  var item = __nhCache[k];
  if (!item) return '';
  if (item.expires && Date.now() > item.expires) { delete __nhCache[k]; return ''; }
  return item.value;
}
function __nhMd5(s) {
  try {
    var crypto = require('crypto');
    return crypto.createHash('md5').update(String(s), 'utf-8').digest('hex');
  } catch (e) { return ''; }
}

function __nhSetContent(value) {
  if (value && typeof value.body === 'function') value = value.body();
  __nhContent = value == null ? '' : String(value);
  return value;
}

function __nhGetString(rule) {
  var parts = String(rule || '').split('@').filter(function (p) { return p.trim(); });
  if (!parts.length) return '';
  var current = new __nhDocument(__nhContent);
  var first = parts.shift().trim();
  current = current.select(first).first();
  if (!current) return '';
  for (var i = 0; i < parts.length; i++) {
    var part = parts[i].trim();
    if (part === 'text') return current.text();
    if (part === 'html') return current.html();
    if (part === 'outerHtml') return current.outerHtml();
    if (part === 'ownText') return current.ownText();
    if (part.indexOf('attr.') === 0) return current.attr(part.slice(5));
    var tag = part.match(/^tag\.([^\.]+)(?:\.(\d+))?$/);
    if (tag) {
      var children = current.select(tag[1]);
      current = children.get(tag[2] ? parseInt(tag[2], 10) : 0);
      if (!current) return '';
    }
  }
  return current.text();
}

var java = {
  // ---- HTTP: Legado java.get / java.post return a Response object ----
  get: function (url, headers) {
    return new __nhResponse(url, __nhCurlRaw(url, 'GET', null, headers), 200, {});
  },
  post: function (url, body, headers) {
    return new __nhResponse(url, __nhCurlRaw(url, 'POST', body, headers), 200, {});
  },
  // ---- java.ajax: accepts "url,{json options}" (Legado) or an object ----
  ajax: function (opts) {
    if (typeof opts === 'string') {
      var text = opts;
      var url = text;
      var option = {};
      var m = text.match(/^(\S+?)\s*,\s*(\{.*\})\s*$/s);
      if (m) {
        url = m[1].trim();
        try { option = JSON.parse(m[2]); } catch (e) { option = {}; }
      }
      return __nhCurlRaw(url, (option.method || 'GET').toUpperCase(),
        option.body != null ? option.body : null, option.headers || {});
    }
    opts = opts || {};
    return __nhCurlRaw(opts.url, (opts.method || 'GET').toUpperCase(),
      opts.body != null ? opts.body : null, opts.headers || {});
  },
  // ---- Content helpers used by older YueDu source scripts ----
  setContent: function (value) { return __nhSetContent(value); },
  getString: function (rule) { return __nhGetString(rule); },
  // ---- legacy variable store (kept for compatibility) ----
  put: function (k, v) { __nhCache[String(k)] = { value: v, expires: 0 }; return v; },
  get: function (url, headers) { return java.httpGet(url, headers); },
  httpGet: function (url, headers) {
    return new __nhResponse(url, __nhCurlRaw(url, 'GET', null, headers), 200, {});
  },
  getCookie: function () { return __nhCookieJar.join('; '); },
  setCookie: function (c) { if (c) __nhCookieJar.push(String(c)); return c; },
  getCookies: function () { return __nhCookieJar.slice(); },
  getLoginInfo: function () { return null; },
  getLoginInfoMap: function () { return null; },
  startBrowserAwait: function (url, msg) {
    throw new Error('startBrowserAwait: 页面需要浏览器验证/输入验证码，无法自动处理: ' + msg);
  },
  getVerificationCode: function (url) {
    throw new Error('getVerificationCode: 该章节需要人工输入验证码，服务器端无法自动处理: ' + url);
  },
  stringToBase64: function (s) { return Buffer.from(String(s), 'utf-8').toString('base64'); },
  base64ToString: function (s) { try { return Buffer.from(String(s), 'base64').toString('utf-8'); } catch (e) { return ''; } },
  base64Encode: function (s) { return java.stringToBase64(s); },
  base64Decode: function (s) { return java.base64ToString(s); },
  hexDecodeToString: function (hex) {
    try {
      var clean = String(hex).replace(/\s+/g, '');
      if (clean.length % 2 !== 0) clean = clean.slice(0, -1);
      return Buffer.from(clean, 'hex').toString('utf-8');
    } catch (e) { return ''; }
  },
  stringToHex: function (s) { return Buffer.from(String(s), 'utf-8').toString('hex'); },
  md5Encode: function (s) { return __nhMd5(s); },
  md5: function (s) { return __nhMd5(s); },
  encodeURI: function (s) { return encodeURIComponent(String(s)); },
  encodeURIComponent: function (s) { return encodeURIComponent(String(s)); },
  decodeURI: function (s) { try { return decodeURIComponent(String(s)); } catch (e) { return String(s); } },
  longToast: function (msg) { return null; },
  toast: function (msg) { return null; },
  log: function (msg) { return null; },
  showDialog: function (msg) { return null; },
  refreshTocUrl: function () { return null; },
  random: function (min, max) {
    if (max === undefined) { max = min; min = 0; }
    return Math.floor(Math.random() * (max - min)) + min;
  },
};

var source = {
  getVariable: function () { return JSON.stringify(__nhVars); },
  put: function (k, v) { __nhVars[String(k)] = v; return v; },
  get: function (k) { var v = __nhVars[String(k)]; return v === undefined ? '' : v; },
  remove: function (k) { delete __nhVars[String(k)]; },
  getLoginInfo: function () { return null; },
  getLoginInfoMap: function () { return null; },
  getCookie: function () { return __nhCookieJar.join('; '); },
  setCookie: function (c) { if (c) __nhCookieJar.push(String(c)); return c; },
};
['bookSourceUrl', 'bookSourceName', 'bookSourceGroup', 'bookSourceType',
 'bookUrlPattern', 'customOrder', 'loginUrl', 'header', 'searchUrl'].forEach(function (key) {
  Object.defineProperty(source, key, {
    get: function () { return __nhSourceConfig[key] || ''; },
    configurable: true,
  });
});

var cookie = {
  getCookie: function () { return __nhCookieJar.join('; '); },
  setCookie: function (c) { if (c) __nhCookieJar.push(String(c)); return c; },
  getCookies: function () { return __nhCookieJar.slice(); },
};

var cache = {
  get: function (k) { return __nhCacheGet(k); },
  put: function (k, v, ttl) {
    var expires = 0;
    if (ttl) { var n = parseFloat(ttl); if (n > 0) expires = Date.now() + n * 1000; }
    __nhCache[String(k)] = { value: v, expires: expires };
    return v;
  },
  delete: function (k) { delete __nhCache[String(k)]; return true; },
  deleteMemory: function (k) { delete __nhCache[String(k)]; return true; },
  remove: function (k) { delete __nhCache[String(k)]; return true; },
  contains: function (k) { return __nhCacheGet(k) !== ''; },
  getMemory: function (k) { return __nhCacheGet(k); },
  putMemory: function (k, v) { return cache.put(k, v); },
  getLongMemory: function (k) { return __nhCacheGet(k); },
  putLongMemory: function (k, v) { return cache.put(k, v); },
};

// ---- Legado globals ----
function Get(key) {
  key = String(key);
  if (key in __nhVars && __nhVars[key] !== undefined) return __nhVars[key];
  return '';
}
function Put(key, value) { __nhVars[String(key)] = value; return value; }
function Set(key, value) { return Put(key, value); }
function sleep(ms) {
  ms = parseInt(ms, 10) || 0;
  if (ms <= 0) return;
  try {
    var sab = new SharedArrayBuffer(4);
    var ia = new Int32Array(sab);
    Atomics.wait(ia, 0, 0, ms);
  } catch (e) {
    var end = Date.now() + ms;
    while (Date.now() < end) { /* busy wait */ }
  }
}
function Rate() { return 800; }
function Reload(url) {
  var headers = {
    'User-Agent': 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
  };
  if (__nhSourceConfig.bookSourceUrl) headers['Referer'] = __nhSourceConfig.bookSourceUrl;
  var text = __nhCurlRaw(url, 'GET', null, headers, 40);
  return text == null ? '' : text;
}
function Url() {
  return (typeof baseUrl !== 'undefined' && baseUrl) ? baseUrl : (__nhVars.baseUrl || '');
}

function __nhSetSourceConfig(cfg) {
  if (!cfg) return;
  for (var k in cfg) { if (cfg[k] !== undefined) __nhSourceConfig[k] = cfg[k]; }
}
function __nhSetVars(vars) {
  if (!vars) return;
  for (var k in vars) { if (vars[k] !== undefined) __nhVars[k] = vars[k]; }
}

if (typeof globalThis !== 'undefined') {
  globalThis.__nhVars = __nhVars;
  globalThis.__nhSetSourceConfig = __nhSetSourceConfig;
  globalThis.__nhSetVars = __nhSetVars;
  globalThis.__nhSetContent = __nhSetContent;
  globalThis.__nhSourceConfig = __nhSourceConfig;
  globalThis.java = java;
  globalThis.source = source;
  globalThis.cookie = cookie;
  globalThis.cache = cache;
  globalThis.Get = Get;
  globalThis.Put = Put;
  globalThis.Set = Set;
  globalThis.Reload = Reload;
  globalThis.sleep = sleep;
  globalThis.Rate = Rate;
  globalThis.Url = Url;
}
