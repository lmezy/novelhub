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
  els.selectFirst = function (css) {
    for (var i = 0; i < els.length; i++) {
      var found = els[i].selectFirst(css);
      if (found) return found;
    }
    return null;
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
      // The operator list must include plain ``=``: without it ``[class=x]``
      // degraded to "has a class attribute" and matched any element, so
      // XPath translated selectors (``//div[@class='info']``) picked the
      // wrong node.
      var am = inner.match(/^([\w:.-]+)(?:\s*(~=|\^=|\$=|\*=|\|=|=)\s*(.*?))?$/);
      if (!am) return false;
      var name = am[1].toLowerCase();
      var op = am[2] || '';
      var val = am[3] !== undefined ? am[3].replace(/^["']|["']$/g, '') : '';
      var attrVal = el.attrs[name];
      if (attrVal === undefined) return false;
      if (!op) return true;
      if (op === '=') return attrVal === val;
      if (op === '~=') return (' ' + attrVal + ' ').indexOf(' ' + val + ' ') >= 0;
      if (op === '|=') return attrVal === val || attrVal.indexOf(val + '-') === 0;
      if (op === '^=') return attrVal.indexOf(val) === 0;
      if (op === '$=') return attrVal.length >= val.length && attrVal.slice(attrVal.length - val.length) === val;
      if (op === '*=') return attrVal.indexOf(val) >= 0;
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
      } else if (pname === 'nth-of-type') {
        // Used by the XPath translator: ``li[2]`` -> ``li:nth-of-type(2)``.
        var specT = parg.trim();
        if (!/^\d+$/.test(specT)) return false;
        var idxT = 1;
        var prevT = el.previousElementSibling();
        while (prevT) {
          if (prevT.tag === el.tag) idxT++;
          prevT = prevT.previousElementSibling();
        }
        if (idxT !== parseInt(specT, 10)) return false;
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
// jsoup ``Element.selectFirst`` / ``Elements.selectFirst``: return the first
// match or null.  Icu's exploreUrl script guards with ``if (span)``, so a
// missing method made the whole category list come back as an error string.
__nhEl.prototype.selectFirst = function (css) {
  var els = this.select(css);
  return els.length ? els[0] : null;
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
// jsoup ``Node.equals``.  Scripts walk siblings with
// ``while (el && !el.equals(stopEl))`` (Icu's exploreUrl), which threw
// "equals is not a function" before.  Elements come from one shim DOM tree,
// so identity is the faithful comparison.
__nhEl.prototype.equals = function (other) {
  return other === this;
};
__nhEl.prototype.is = function (other) { return this.equals(other); };
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

// Default headers applied to *every* JS-issued request.
//
// `Reload()` already built a Referer from the source URL (Legado does the same),
// but the `java.*` path never did: `java.get` / `java.post` / `java.ajax` passed
// the caller's headers straight through, and only `java.connect` used
// `__nhSourceHeaders`.  Sites that check the referer therefore saw a bare request
// from any source whose rules fetch through `java.*`.
//
// Applied here, at the single funnel every request goes through, rather than at
// the four call sites.  A caller-supplied Referer always wins.
function __nhFinalHeaders(headers) {
  var out = {};
  if (headers) {
    for (var k in headers) {
      if (headers[k] !== undefined && headers[k] !== null) out[k] = headers[k];
    }
  }
  if (!('Referer' in out) && __nhSourceConfig.bookSourceUrl) {
    out['Referer'] = __nhSourceConfig.bookSourceUrl;
  }
  return out;
}

function __nhCurlRaw(url, method, body, headers, timeoutSec) {
  var execSync = require('child_process').execSync;
  headers = __nhFinalHeaders(headers);
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

// Merge the source's own ``header`` rule with an optional JSON header
// argument, the way Legado's AnalyzeUrl seeds every java.* request.
function __nhSourceHeaders(extraJson) {
  var headers = {};
  function merge(raw) {
    if (!raw) return;
    var parsed = raw;
    if (typeof raw === 'string') {
      try { parsed = JSON.parse(raw); } catch (e) { return; }
    }
    if (!parsed || typeof parsed !== 'object') return;
    for (var k in parsed) {
      if (parsed[k] !== undefined && parsed[k] !== null) headers[k] = String(parsed[k]);
    }
  }
  merge(__nhSourceConfig.header);
  merge(extraJson);
  return headers;
}

function __nhResponse(url, text, status, headers) {
  this._url = url || '';
  this._text = text == null ? '' : text;
  this._status = status || 200;
  this._headers = headers || {};
}
__nhResponse.prototype.body = function () { return this._text; };
// Legado's ``java.connect(url)`` returns a StrResponse, and sources call the
// Java-style getter ``.getBody()`` on it (Icu's exploreUrl).  Without it the
// call threw, the source's own try/catch turned that into a plain error
// string, and discovery silently ended with "发现规则是 Legado JS 脚本".
__nhResponse.prototype.getBody = function () { return this._text; };
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

// Split a rule on '@' step separators, ignoring '@' inside [...], (...) and
// quotes.  Legado's RuleAnalyzer does the same; a naive split tore
// ``//div[@class='x']/text()`` apart and made ``java.getString`` return ''.
function __nhSplitSteps(rule) {
  var text = String(rule == null ? '' : rule);
  var parts = [];
  var buf = '';
  var depth = 0;
  var quote = '';
  for (var i = 0; i < text.length; i++) {
    var c = text[i];
    if (quote) {
      buf += c;
      if (c === '\\' && i + 1 < text.length) { buf += text[++i]; continue; }
      if (c === quote) quote = '';
      continue;
    }
    if (c === '"' || c === "'") { quote = c; buf += c; continue; }
    if (c === '[' || c === '(') depth++;
    else if (c === ']' || c === ')') { if (depth > 0) depth--; }
    else if (c === '@' && depth === 0) {
      parts.push(buf);
      buf = '';
      continue;
    }
    buf += c;
  }
  parts.push(buf);
  return parts.filter(function (p) { return String(p).trim() !== ''; });
}

// Translate the XPath subset book sources use (``//div[@class='x']/ul/li``)
// into the CSS subset the shim implements.  Returns null when the rule uses
// XPath features we cannot express in CSS.
function __nhXPathStepToCss(step) {
  var m = /^([A-Za-z][\w-]*|\*)?((?:\[[^\]]*\])*)$/.exec(String(step || '').trim());
  if (!m) return null;
  var css = m[1] || '*';
  var preds = m[2] || '';
  var re = /\[([^\]]*)\]/g;
  var pm;
  while ((pm = re.exec(preds))) {
    var pred = pm[1].trim();
    if (pred.charAt(0) === '@') {
      var body = pred.slice(1).trim();
      if (!/^[\w:.-]+(\s*(?:[!^$*~|]?=)\s*\S.*)?$/.test(body)) return null;
      css += '[' + body + ']';
      continue;
    }
    if (/^\d+$/.test(pred)) {
      css += ':nth-of-type(' + pred + ')';
      continue;
    }
    return null;
  }
  return css;
}

function __nhXPathToCss(rule) {
  var text = String(rule || '').trim().replace(/^@xpath:/i, '').trim();
  if (!text) return null;
  if (text.charAt(0) !== '/' && text.indexOf('./') !== 0) return null;
  text = text.replace(/^\.?\/\/?/, '');
  if (!text) return null;
  var steps = [];
  var buf = '';
  var combinator = ' ';
  var i = 0;
  while (i < text.length) {
    var c = text[i];
    if (c === '[') {
      var end = text.indexOf(']', i);
      if (end === -1) return null;
      buf += text.slice(i, end + 1);
      i = end + 1;
      continue;
    }
    if (c === '/') {
      var e = i;
      while (e < text.length && text[e] === '/') e++;
      if (!buf.trim()) return null;
      steps.push([combinator, buf.trim()]);
      buf = '';
      combinator = (e - i > 1) ? ' ' : ' > ';
      i = e;
      continue;
    }
    buf += c;
    i++;
  }
  if (buf.trim()) steps.push([combinator, buf.trim()]);
  if (!steps.length) return null;
  var css = '';
  for (var s = 0; s < steps.length; s++) {
    var converted = __nhXPathStepToCss(steps[s][1]);
    if (converted === null) return null;
    css += (s === 0 ? '' : steps[s][0]) + converted;
  }
  return css;
}

function __nhGetString(rule) {
  var text = String(rule == null ? '' : rule).trim();
  if (!text) return '';
  // Legado's ``##regex##replacement`` transform.
  var transform = null;
  var tIdx = text.indexOf('##');
  if (tIdx > 0) {
    var tParts = text.slice(tIdx + 2).split('##');
    if (tParts.length >= 2) {
      transform = [tParts[0], tParts[1]];
      text = text.slice(0, tIdx);
    }
  }
  var parts = __nhSplitSteps(text);
  if (!parts.length) return '';
  var selector = parts.shift().trim();
  // ``//div/span/text()``: keep the trailing accessor as a step.
  var tail = selector.match(/^(.*?)\/(text\(\)|html\(\)|outerHtml|ownText|all)$/);
  if (tail && tail[1]) {
    selector = tail[1].trim();
    parts.unshift(tail[2] === 'text()' ? 'text' : (tail[2] === 'html()' ? 'html' : tail[2]));
  }
  if (selector.charAt(0) === '/' || selector.indexOf('./') === 0) {
    var css = __nhXPathToCss(selector);
    if (css === null) return '';
    selector = css;
  }
  var current = new __nhDocument(__nhContent);
  current = current.select(selector).first();
  if (!current) return '';
  var value = null;
  for (var i = 0; i < parts.length; i++) {
    var part = parts[i].trim();
    if (part === 'text') { value = current.text(); break; }
    if (part === 'html') { value = current.html(); break; }
    if (part === 'outerHtml' || part === 'all') { value = current.outerHtml(); break; }
    if (part === 'ownText') { value = current.ownText(); break; }
    if (part.indexOf('attr.') === 0) { value = current.attr(part.slice(5)); break; }
    var tag = part.match(/^tag\.([^\.]+)(?:\.(\d+))?$/);
    if (tag) {
      var children = current.select(tag[1]);
      current = children.get(tag[2] ? parseInt(tag[2], 10) : 0);
      if (!current) return '';
      continue;
    }
    // ``@href`` / ``@title`` come through as a bare attribute name.
    var attrName = part.replace(/^@/, '');
    if (/^[\w:.-]+$/.test(attrName) && current.attr(attrName) !== '') {
      value = current.attr(attrName);
      break;
    }
  }
  if (value === null) value = current.text();
  value = value == null ? '' : String(value);
  if (transform) {
    try {
      value = value.replace(new RegExp(transform[0], 'g'), transform[1]);
    } catch (e) {
      /* keep the untransformed value */
    }
  }
  return value;
}

// ---- Legado JsEncodeUtils helpers (see docs/legado-rule-spec-diff.md C-22/C-23) ----

/**
 * Normalise a JCE digest name to the OpenSSL spelling Node wants.
 *
 * Legado passes the name straight to hutool/`MessageDigest`, and book sources
 * write both "SHA-1" and "SHA1".  Node accepts "sha1"/"sha256" but not the
 * hyphenated JCE form, so strip the hyphen for the `SHA-<digits>` family only --
 * doing it globally would turn "sha3-256" into "sha3256".
 */
function __nhHashAlgorithm(name) {
  var a = String(name === undefined || name === null ? '' : name)
    .trim().toLowerCase().replace(/\s+/g, '');
  // Java's Mac names are "HmacSHA256"/"HmacMD5"; OpenSSL (Node) wants
  // "sha256"/"md5".  hutool's `HMac(algorithm, key)` takes the Java spelling.
  a = a.replace(/^hmac[-_]?/, '');
  // JCE spells digests "SHA-1"/"SHA-256"; book sources also write "SHA1".  Strip
  // the hyphen for the SHA-<digits> family only -- doing it globally would turn
  // "sha3-256" into "sha3256".
  var m = /^sha-?(\d+)$/.exec(a);
  if (m) return 'sha' + m[1];
  return a;
}

/**
 * Port of Legado's `HtmlFormatter.formatKeepImg` with a null redirect URL.
 *
 * The nine-step pipeline is copied from `utils/HtmlFormatter.kt:24-35`.  Note the
 * tag-stripping regexes there are plain `toRegex()` calls, i.e. **case
 * sensitive** -- only `formatImagePattern` sets `CASE_INSENSITIVE` -- so the
 * character-class patterns below deliberately omit the `i` flag.
 *
 * The `,{...}` URL-option suffix that Legado splits off before absolutising is
 * not handled here: `java.htmlFormat` calls `formatKeepImg(str)` with no
 * redirect URL, and with a null base the split-then-rejoin yields the same
 * string, so the raw URL is equivalent.
 */
function __nhHtmlFormatKeepImg(html) {
  if (html === null || html === undefined) return '';
  var text = String(html)
    .replace(/(&nbsp;)+/g, ' ')
    .replace(/(&ensp;|&emsp;)/g, ' ')
    .replace(/(&thinsp;|&zwnj;|&zwj;|\u2009|\u200C|\u200D)/g, '')
    .replace(/<\/?(?:div|p|br|hr|h\d|article|dd|dl)[^>]*>/g, '\n')
    .replace(/<!--[^>]*-->/g, '')
    .replace(/<\/?(?!img)[a-zA-Z]+(?=[ >])[^<>]*>/g, '')
    .replace(/\s*\n+\s*/g, '\n\u3000\u3000')
    .replace(/^[\n\s]+/, '\u3000\u3000')
    .replace(/[\n\s]+$/, '');

  var imgPattern = /<img[^>]*\ssrc\s*=\s*['"]([^'"<>]*\{[^}]+\})['"][^>]*>|<img[^>]*\s(?:data-src|src)\s*=\s*['"]([^'">]+)['"][^>]*>|<img[^>]*\sdata-[^=>]*=\s*['"]([^'">]*)['"][^>]*>/gi;
  var out = '';
  var pos = 0;
  var m;
  while ((m = imgPattern.exec(text)) !== null) {
    out += text.slice(pos, m.index);
    var src = m[1] || m[2] || m[3] || '';
    out += '<img src="' + src + '">';
    pos = m.index + m[0].length;
  }
  if (pos < text.length) out += text.slice(pos);
  return out;
}

// ---- Legado symmetric crypto (JsEncodeUtils.kt + help/crypto/SymmetricCryptoAndroid.kt) ----

/**
 * Legado's `isHex()`: whether a string is a hex byte string.
 *
 * Also requires an even length here.  `HexUtil.decodeHex` throws on an
 * odd-length string in Legado, so requiring an even length lets such a value
 * fall through to the Base64 branch instead of failing the whole rule.
 * Identical whenever Legado succeeds, strictly more forgiving where it throws.
 */
function __nhIsHex(s) {
  var t = String(s === undefined || s === null ? '' : s).replace(/\s+/g, '');
  return t.length > 0 && t.length % 2 === 0 && /^[0-9a-fA-F]+$/.test(t);
}

/** Decode a ciphertext argument the way `SymmetricCryptoAndroid.decrypt` does. */
function __nhCryptoDecode(data) {
  var s = String(data === undefined || data === null ? '' : data);
  if (__nhIsHex(s)) return Buffer.from(s.replace(/\s+/g, ''), 'hex');
  return Buffer.from(s, 'base64');
}

/** Map a Java/JCE transformation onto a Node cipher name. */
function __nhCipherName(transformation, keyLen) {
  var parts = String(transformation || 'AES').split('/');
  var algo = (parts[0] || 'AES').trim().toUpperCase();
  var mode = (parts[1] || 'ECB').trim().toLowerCase();
  var base;
  if (algo === 'AES') {
    base = 'aes-' + (keyLen === 24 ? 192 : (keyLen === 32 ? 256 : 128));
  } else if (algo === 'DESEDE' || algo === 'TRIPLEDES' || algo === '3DES') {
    base = keyLen === 16 ? 'des-ede' : 'des-ede3';
  } else if (algo === 'DES') {
    base = 'des';
  } else {
    base = algo.toLowerCase();
  }
  return base + '-' + mode;
}

/** JCE "NoPadding" turns Node's PKCS#7 auto-padding off. */
function __nhCipherAutoPadding(transformation) {
  var padding = (String(transformation || '').split('/')[2] || 'PKCS5Padding')
    .trim().toLowerCase();
  return padding.indexOf('nopadding') === -1;
}

/**
 * Port of Legado's `createSymmetricCrypto(transformation, key, iv)`.
 *
 * Legado returns a hutool `SymmetricCrypto`; only the five methods book sources
 * call are provided.  The returned object builds a fresh cipher per operation,
 * because a Node Cipher cannot be reused after `final()`.
 *
 * **Known gap**: hutool's `SymmetricCrypto(String, byte[])` normalises a key or
 * IV whose length is invalid for the algorithm.  That logic lives in hutool -- a
 * gradle dependency, not in this repository -- so it is **not** replicated here;
 * an invalid length raises instead of being silently padded.  Standard lengths
 * (AES 16/24/32, DES 8, 3DES 24) are exact, and the AES-128-ECB path is verified
 * against the FIPS-197 vector in the tests.
 */
function __nhSymmetricCrypto(transformation, key, iv) {
  var keyBytes = Buffer.isBuffer(key) ? key
    : (key === undefined || key === null ? Buffer.alloc(0)
      : Buffer.from(String(key), 'utf-8'));
  var ivBytes = Buffer.isBuffer(iv) ? iv
    : (iv === undefined || iv === null || iv === '' ? null
      : Buffer.from(String(iv), 'utf-8'));
  var name = __nhCipherName(transformation, keyBytes.length);
  var autoPadding = __nhCipherAutoPadding(transformation);

  function cipher(encrypting) {
    var ivArg = (ivBytes && ivBytes.length) ? ivBytes : null;
    try {
      var c = encrypting
        ? require('crypto').createCipheriv(name, keyBytes, ivArg)
        : require('crypto').createDecipheriv(name, keyBytes, ivArg);
      c.setAutoPadding(autoPadding);
      return c;
    } catch (e) {
      // Single DES needs OpenSSL 3's legacy provider, which Node 17+ leaves off:
      // "des-ecb"/"des-cbc" are absent from crypto.getCiphers() and every call
      // fails with ERR_OSSL_EVP_UNSUPPORTED.  Re-throw with the reason attached
      // so the book source's own try/catch -- and the crawler log -- say *why*,
      // instead of a bare "unsupported" (3DES is unaffected: des-ede3-* exists).
      var singleDes = /^des-/.test(name) && !/^des-ede/.test(name);
      throw new Error(
        'createSymmetricCrypto(' + transformation + ') 失败: '
        + (e && e.message ? e.message : String(e))
        + (singleDes
          ? ' —— 单 DES 在 OpenSSL 3 下需 legacy provider，本运行时不可用'
          : '')
      );
    }
  }

  function toBuffer(data) {
    return Buffer.isBuffer(data) ? data : Buffer.from(String(data), 'utf-8');
  }

  function run(encrypting, data) {
    var c = cipher(encrypting);
    return Buffer.concat([c.update(data), c.final()]);
  }

  return {
    encrypt: function (data) { return run(true, toBuffer(data)); },
    encryptBase64: function (data) {
      return run(true, toBuffer(data)).toString('base64');
    },
    encryptHex: function (data) { return run(true, toBuffer(data)).toString('hex'); },
    decrypt: function (data) { return run(false, __nhCryptoDecode(data)); },
    decryptStr: function (data) { return run(false, __nhCryptoDecode(data)).toString('utf-8'); },
  };
}

// ---- Legado byte/charset helpers (JsExtensions.kt) ----

/**
 * Map a Java charset name onto a Node Buffer encoding.
 *
 * Node's Buffer only knows utf-8/latin1/utf-16le/ascii.  The Chinese charsets a
 * book source may ask for (GBK/GB2312/GB18030/Big5) are **not** available --
 * they would need an iconv dependency -- so they raise a readable error instead
 * of silently producing mojibake.
 */
function __nhBufferEncoding(name) {
  var c = String(name === undefined || name === null ? 'UTF-8' : name)
    .trim().toLowerCase().replace(/[-_]/g, '');
  if (c === '' || c === 'utf8') return 'utf-8';
  if (c === 'iso88591' || c === 'latin1') return 'latin1';
  if (c === 'utf16' || c === 'utf16le' || c === 'unicode') return 'utf16le';
  if (c === 'ascii' || c === 'usascii') return 'ascii';
  if (c === 'gbk' || c === 'gb2312' || c === 'gb18030' || c === 'big5'
      || c === 'eucjp' || c === 'shiftjis' || c === 'euckr') {
    throw new Error(
      'charset ' + name + ' 在 Node 侧无内建支持'
      + '（Buffer 仅支持 utf-8/latin1/utf-16le/ascii），需要 iconv 依赖才能实现'
    );
  }
  return c; // let Buffer reject anything else
}

/**
 * The port as *written* in a URL string, or -1.
 *
 * Needed because the WHATWG URL normalises a scheme's default port away:
 * `new URL('http://x:80/p').port` is `''`, while Java's `URL.getPort()` returns
 * 80.  Legado builds `origin` from `URL.getPort()`, so the explicit port has to
 * be recovered from the raw text or `http://x:80` would come back as `http://x`.
 */
function __nhExplicitPort(s) {
  var m = /^[a-zA-Z][a-zA-Z0-9+.-]*:\/\/[^/?#]*?:(\d+)(?=[/?#]|$)/
    .exec(String(s === undefined || s === null ? '' : s));
  return m ? parseInt(m[1], 10) : -1;
}

/**
 * Port of Legado's `java.toURL` (`utils/JsURL.kt`).
 *
 * Legado parses with `java.net.URL`, and two of its behaviours differ from the
 * WHATWG URL Node provides, so both are reproduced explicitly:
 *
 * 1. `origin` keeps a port that was written out, even the scheme's default one
 *    (see `__nhExplicitPort`).  A relative URL inherits the port from `baseUrl`.
 * 2. Values are decoded with `URLDecoder`, which turns `+` into a space;
 *    `decodeURIComponent` does not, so `+` is replaced first.
 */
function __nhJsURL(url, baseUrl) {
  var raw = String(url);
  var hasBase = baseUrl !== undefined && baseUrl !== null && baseUrl !== '';
  var u = hasBase ? new URL(raw, String(baseUrl)) : new URL(raw);

  var port = __nhExplicitPort(raw);
  if (port < 0 && !/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw)) {
    port = __nhExplicitPort(baseUrl); // relative URL: the authority is the base's
  }
  if (port < 0) port = u.port === '' ? -1 : parseInt(u.port, 10);

  var searchParams = null;
  if (u.search && u.search.length > 1) {
    searchParams = {};
    u.search.slice(1).split('&').forEach(function (pair) {
      var kv = pair.split('=');
      if (kv.length >= 2) {
        // Java: split("=", limit = 2) then URLDecoder on the remainder.
        searchParams[kv[0]] = decodeURIComponent(
          kv.slice(1).join('=').replace(/\+/g, ' ')
        );
      }
    });
  }
  return {
    host: u.hostname,
    origin: port > 0
      ? u.protocol + '//' + u.hostname + ':' + port
      : u.protocol + '//' + u.hostname,
    pathname: u.pathname,
    searchParams: searchParams,
  };
}

// ---- Legado chapter-number helpers (utils/StringUtils.kt, JsExtensions.kt:916) ----

/** `StringUtils.fullToHalf`: full-width space and ！..～ become half-width. */
function __nhFullToHalf(input) {
  var s = String(input === undefined || input === null ? '' : input);
  var out = '';
  for (var i = 0; i < s.length; i++) {
    var code = s.charCodeAt(i);
    if (code === 12288) out += ' ';
    else if (code >= 65281 && code <= 65374) out += String.fromCharCode(code - 65248);
    else out += s[i];
  }
  return out;
}

/** `StringUtils.chnMap`: two numeral sets plus the 两/百/千/万/亿 multipliers. */
var __nhChnMap = (function () {
  var map = {};
  var lower = '零一二三四五六七八九十';
  var upper = '〇壹贰叁肆伍陆柒捌玖拾';
  for (var i = 0; i <= 10; i++) {
    map[lower[i]] = i;
    map[upper[i]] = i;
  }
  map['两'] = 2;
  map['百'] = 100;
  map['佰'] = 100;
  map['千'] = 1000;
  map['仟'] = 1000;
  map['万'] = 10000;
  map['亿'] = 100000000;
  return map;
})();

/**
 * `StringUtils.chineseNumToInt`.
 *
 * Legado's first branch -- the "一零二五" digit-by-digit form -- is **dead
 * code**: its guard is `cn.size > 1 && chNum.matches("^[单字符]$")`, which can
 * never hold, because that regex only matches a one-character string.  It is
 * therefore not ported; the loop below already yields 1025 for that input, so
 * behaviour is unchanged.
 *
 * Returns -1 for an unmapped character, mirroring Legado's
 * `runCatching { … }.getOrDefault(-1)` -- there `ChnMap[c]!!` throws on the
 * unknown char.
 */
function __nhChineseNumToInt(chNum) {
  var cn = String(chNum).split('');
  var result = 0, tmp = 0, billion = 0;
  try {
    for (var i = 0; i < cn.length; i++) {
      var cur = __nhChnMap[cn[i]];
      if (cur === undefined) throw new Error('unmapped char: ' + cn[i]);
      if (cur === 100000000) {
        result += tmp;
        result *= cur;
        billion = billion * 100000000 + result;
        result = 0;
        tmp = 0;
      } else if (cur === 10000) {
        result += tmp;
        result *= cur;
        tmp = 0;
      } else if (cur >= 10) {
        if (tmp === 0) tmp = 1;
        result += cur * tmp;
        tmp = 0;
      } else {
        var prev = i >= 1 ? __nhChnMap[cn[i - 1]] : undefined;
        // Kotlin's Int division truncates, so 一千二 -> 1200 needs a floor here.
        tmp = (i >= 2 && i === cn.length - 1 && prev > 10)
          ? Math.floor(cur * prev / 10)
          : tmp * 10 + cur;
      }
    }
    result += tmp + billion;
    return result;
  } catch (e) {
    return -1;
  }
}

/** `StringUtils.stringToInt`. */
function __nhStringToInt(str) {
  if (str === null || str === undefined) return -1;
  var num = __nhFullToHalf(str).replace(/\s+/g, '');
  // Java's Integer.parseInt is strict -- it rejects "12abc", which JS parseInt
  // would cheerfully read as 12.
  if (/^[+-]?\d+$/.test(num)) {
    var parsed = parseInt(num, 10);
    return isNaN(parsed) ? -1 : parsed;
  }
  return __nhChineseNumToInt(num);
}

// ---- Legado date formatting (JsExtensions.kt:512-525, AppConst.kt:38) ----

/**
 * Format an instant with a Java `SimpleDateFormat` pattern subset.
 *
 * Implemented by shifting the instant by `offsetMs` and then reading the *UTC*
 * getters, which is equivalent to formatting in a timezone with that offset and
 * needs no timezone database.
 *
 * `SimpleDateFormat(pattern, Locale.getDefault())` also localises textual fields
 * (`MMM`, `EEE`) from the device locale; those need per-locale tables, so they
 * raise a readable error rather than silently emitting the pattern letter.
 * Numeric fields -- the ones real `timeFormat` patterns use -- are exact.
 */
function __nhFormatDate(ms, pattern, offsetMs) {
  var d = new Date(Number(ms) + (Number(offsetMs) || 0));
  var p = String(pattern === undefined || pattern === null ? '' : pattern);
  var out = '';
  var pad = function (n, width) {
    var s = String(Math.abs(Math.trunc(n)));
    while (s.length < width) s = '0' + s;
    return s;
  };

  for (var i = 0; i < p.length;) {
    var ch = p[i];
    if (ch === "'") { // Java quoting: '' is a literal quote
      if (p[i + 1] === "'") { out += "'"; i += 2; continue; }
      var close = p.indexOf("'", i + 1);
      if (close === -1) { out += p.slice(i + 1); break; }
      out += p.slice(i + 1, close);
      i = close + 1;
      continue;
    }
    var n = 1;
    while (i + n < p.length && p[i + n] === ch) n++;
    var field;
    switch (ch) {
      case 'y':
        field = n === 2 ? pad(d.getUTCFullYear() % 100, 2) : pad(d.getUTCFullYear(), n);
        break;
      case 'M':
        if (n >= 3) {
          // Java: MMM/MMMM are the localised month NAME, not a number.
          throw new Error(
            'SimpleDateFormat 模式 "M"×' + n + ' (在 "' + p + '" 中) 是本地化月份名，'
            + 'Node 侧未实现；请改用 M/MM'
          );
        }
        field = n === 2 ? pad(d.getUTCMonth() + 1, 2) : String(d.getUTCMonth() + 1);
        break;
      case 'd':
        field = n >= 2 ? pad(d.getUTCDate(), 2) : String(d.getUTCDate());
        break;
      case 'H': // 0-23
        field = n >= 2 ? pad(d.getUTCHours(), 2) : String(d.getUTCHours());
        break;
      case 'h': { // 1-12
        var h12 = d.getUTCHours() % 12;
        field = n >= 2 ? pad(h12 === 0 ? 12 : h12, 2) : String(h12 === 0 ? 12 : h12);
        break;
      }
      case 'm':
        field = n >= 2 ? pad(d.getUTCMinutes(), 2) : String(d.getUTCMinutes());
        break;
      case 's':
        field = n >= 2 ? pad(d.getUTCSeconds(), 2) : String(d.getUTCSeconds());
        break;
      case 'S':
        field = pad(d.getUTCMilliseconds(), n);
        break;
      case 'a':
        field = d.getUTCHours() < 12 ? 'AM' : 'PM';
        break;
      default:
        if (/[a-zA-Z]/.test(ch)) {
          throw new Error(
            'SimpleDateFormat 模式 "' + ch + '" (在 "' + p + '" 中) 依赖区域设置，'
            + 'Node 侧未实现；请改用数字字段 (y/M/d/H/h/m/s/S/a)'
          );
        }
        field = ch; // not a pattern letter -> literal
    }
    out += field;
    i += n;
  }
  return out;
}

var java = {
  // ---- HTTP: Legado java.get / java.post return a Response object ----
  get: function (url, headers) {
    return new __nhResponse(url, __nhCurlRaw(url, 'GET', null, headers), 200, {});
  },
  post: function (url, body, headers) {
    return new __nhResponse(url, __nhCurlRaw(url, 'POST', body, headers), 200, {});
  },
  // ---- java.connect(url[, headerJson]): Legado performs the request and
  // returns a StrResponse, so ``java.connect(url).getBody()`` must give the
  // page source (Icu builds its whole explore category list that way).  The
  // source's own ``header`` rule is applied like Legado's AnalyzeUrl does.
  connect: function (url, header) {
    var headers = __nhSourceHeaders(header);
    var text = __nhCurlRaw(url, 'GET', null, headers, 40);
    return new __nhResponse(String(url), text == null ? '' : text, 200, {});
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
  // Legado has two overloads: ``java.get(key)`` reads what ``java.put`` stored,
  // ``java.get(url, headers)`` performs an HTTP GET.  Treating the one-argument
  // form as HTTP made ``JSON.parse(java.get('imgInfoList') || '[]')`` (绅士漫画's
  // ruleContent) parse an empty response body and throw.
  get: function (key, headers) {
    var isUrl = arguments.length >= 2 || /^(https?:)?\/\//.test(String(key));
    if (isUrl) return java.httpGet(key, headers);
    var stored = __nhCacheGet(String(key));
    return stored === undefined || stored === null ? '' : stored;
  },
  httpGet: function (url, headers) {
    return new __nhResponse(url, __nhCurlRaw(url, 'GET', null, headers), 200, {});
  },
  getCookie: function () { return __nhCookieJar.join('; '); },
  setCookie: function (c) { if (c) __nhCookieJar.push(String(c)); return c; },
  getCookies: function () { return __nhCookieJar.slice(); },
  getLoginInfo: function () { return null; },
  getLoginInfoMap: function () { return null; },
  // Legado returns the WebView UA; sources build their ``header`` rule with it
  // (要撸小说), so a missing function broke the whole header evaluation.
  getWebViewUA: function () {
    return "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36";
  },
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
  // ---- Legado byte / charset / URL helpers (JsExtensions.kt:388-…, 916-924) --
  strToBytes: function (str, charsetName) {
    return Buffer.from(String(str), __nhBufferEncoding(charsetName));
  },
  bytesToStr: function (bytes, charsetName) {
    return Buffer.from(bytes).toString(__nhBufferEncoding(charsetName));
  },
  base64DecodeToByteArray: function (str) {
    // Legado returns null for a blank input (`isNullOrBlank`).
    if (str === null || str === undefined || String(str).trim() === '') return null;
    return Buffer.from(String(str), 'base64');
  },
  hexDecodeToByteArray: function (hex) {
    var clean = String(hex === undefined || hex === null ? '' : hex).replace(/\s+/g, '');
    if (clean.length % 2 !== 0) clean = clean.slice(0, -1);
    return Buffer.from(clean, 'hex');
  },
  hexEncodeToString: function (utf8) {
    // hutool `HexUtil.encodeHexStr(String)` hashes the string's UTF-8 bytes.
    return Buffer.from(String(utf8), 'utf-8').toString('hex');
  },
  randomUUID: function () {
    // Java's `UUID.randomUUID().toString()`: lowercase, hyphenated, v4.
    return require('crypto').randomUUID();
  },
  toURL: function (url, baseUrl) { return __nhJsURL(url, baseUrl); },
  toNumChapter: function (s) {
    // JsExtensions.kt:916-924 -- `titleNumPattern` is `(第)(.+?)(章)`; when it
    // does not match, the input is returned unchanged.
    if (s === null || s === undefined) return null;
    var str = String(s);
    var m = /第(.+?)章/.exec(str);
    if (!m) return str;
    return '第' + __nhStringToInt(m[1]) + '章';
  },
  timeFormat: function (time) {
    // JsExtensions.kt:523-525 -> `AppConst.dateFormat.format(Date(time))`, and
    // `AppConst.dateFormat` is the fixed pattern "yyyy/MM/dd HH:mm"
    // (AppConst.kt:38).  Formatting happens in the runtime's default timezone;
    // getTimezoneOffset() makes that correct across DST with no tz database.
    var ms = Number(time);
    return __nhFormatDate(
      ms, 'yyyy/MM/dd HH:mm', -new Date(ms).getTimezoneOffset() * 60000
    );
  },
  timeFormatUTC: function (time, format, sh) {
    // JsExtensions.kt:512-518 -> `SimpleDateFormat(format)` with
    // `timeZone = SimpleTimeZone(sh, "UTC")`.  `SimpleTimeZone`'s rawOffset is in
    // **milliseconds**, so `sh` is milliseconds here too: a caller passing 8 gets
    // ~UTC (8 ms), and one passing 28800000 gets UTC+8.  Kept faithful rather
    // than "helpfully" treating small values as hours.
    return __nhFormatDate(Number(time), format, Number(sh) || 0);
  },
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
  // ---- JsEncodeUtils: digests and HMACs -------------------------------------
  // Legado: `digestHex(data, algorithm)` = `DigestUtil.digester(algorithm)
  // .digestHex(data)` (JsEncodeUtils.kt:438-443), and hutool digests the UTF-8
  // bytes of the string.  `HMacHex(data, algorithm, key)` =
  // `HMac(algorithm, key.toByteArray()).digestHex(data)` (:468-474), i.e. the key
  // is also UTF-8 bytes.
  md5Encode16: function (s) {
    // MD5Utils.md5Encode16: `md5Encode(str).substring(8, 24)`.
    return __nhMd5(s).substring(8, 24);
  },
  digestHex: function (data, algorithm) {
    try {
      return require('crypto').createHash(__nhHashAlgorithm(algorithm))
        .update(String(data), 'utf-8').digest('hex');
    } catch (e) { return ''; }
  },
  digestBase64Str: function (data, algorithm) {
    try {
      // Base64.NO_WRAP: standard base64, no line breaks.
      return require('crypto').createHash(__nhHashAlgorithm(algorithm))
        .update(String(data), 'utf-8').digest('base64');
    } catch (e) { return ''; }
  },
  HMacHex: function (data, algorithm, key) {
    try {
      return require('crypto').createHmac(
        __nhHashAlgorithm(algorithm), Buffer.from(String(key), 'utf-8')
      ).update(String(data), 'utf-8').digest('hex');
    } catch (e) { return ''; }
  },
  HMacBase64: function (data, algorithm, key) {
    try {
      return require('crypto').createHmac(
        __nhHashAlgorithm(algorithm), Buffer.from(String(key), 'utf-8')
      ).update(String(data), 'utf-8').digest('base64');
    } catch (e) { return ''; }
  },
  htmlFormat: function (str) { return __nhHtmlFormatKeepImg(str); },
  createSymmetricCrypto: function (transformation, key, iv) {
    // Deliberately NOT wrapped in try/catch: Legado's own createSymmetricCrypto
    // throws too, and an unavailable algorithm must surface as a readable error
    // rather than a null that silently empties the field.
    return __nhSymmetricCrypto(transformation, key, iv);
  },
  // ---- AES family (JsEncodeUtils.kt:91-279) --------------------------------
  // These are faithful ports of Legado's own (deprecated) wrappers, including two
  // inconsistencies that a "sensible" reimplementation would silently fix -- and
  // fixing them would diverge from the book sources written against Legado:
  //
  //   1. `aesEncodeToString` is documented as "encrypt AES to String" but its
  //      body calls `.decryptStr(data)` -- it *decrypts*.
  //   2. `aesDecodeArgsBase64Str` Base64-decodes key/iv, while
  //      `aesEncodeArgsBase64Str` passes them through raw, even though both
  //      document the key as "Base64后的密钥".
  //
  // Also note the `*Base64*` decode variants are byte-identical to the plain ones:
  // `SymmetricCryptoAndroid.decrypt(String)` already auto-detects hex vs Base64.
  aesDecodeToByteArray: function (str, key, transformation, iv) {
    var c = java.createSymmetricCrypto(transformation, key, iv);
    return c ? c.decrypt(str) : null;
  },
  aesDecodeToString: function (str, key, transformation, iv) {
    var c = java.createSymmetricCrypto(transformation, key, iv);
    return c ? c.decryptStr(str) : null;
  },
  aesBase64DecodeToByteArray: function (str, key, transformation, iv) {
    return java.aesDecodeToByteArray(str, key, transformation, iv);
  },
  aesBase64DecodeToString: function (str, key, transformation, iv) {
    return java.aesDecodeToString(str, key, transformation, iv);
  },
  aesEncodeToByteArray: function (data, key, transformation, iv) {
    var c = java.createSymmetricCrypto(transformation, key, iv);
    return c ? c.encrypt(data) : null;
  },
  aesEncodeToString: function (data, key, transformation, iv) {
    // Legado's body is `.decryptStr(data)` despite the name -- kept as-is.
    var c = java.createSymmetricCrypto(transformation, key, iv);
    return c ? c.decryptStr(data) : null;
  },
  aesEncodeToBase64ByteArray: function (data, key, transformation, iv) {
    var c = java.createSymmetricCrypto(transformation, key, iv);
    return c ? Buffer.from(c.encryptBase64(data), 'utf-8') : null;
  },
  aesEncodeToBase64String: function (data, key, transformation, iv) {
    var c = java.createSymmetricCrypto(transformation, key, iv);
    return c ? c.encryptBase64(data) : null;
  },
  aesDecodeArgsBase64Str: function (data, key, mode, padding, iv) {
    var c = java.createSymmetricCrypto(
      'AES/' + mode + '/' + padding,
      Buffer.from(String(key), 'base64'),
      Buffer.from(String(iv), 'base64')
    );
    return c ? c.decryptStr(data) : null;
  },
  aesEncodeArgsBase64Str: function (data, key, mode, padding, iv) {
    // Legado does NOT Base64-decode key/iv here (unlike the decode variant).
    var c = java.createSymmetricCrypto('AES/' + mode + '/' + padding, key, iv);
    return c ? c.encryptBase64(data) : null;
  },
  // ---- DES family (JsEncodeUtils.kt:281-320) --------------------------------
  // Single DES cannot work on this runtime (OpenSSL 3 legacy provider, see
  // __nhSymmetricCrypto).  The wrappers still exist so a source calling them gets
  // a readable reason rather than "java.desDecodeToString is not a function".
  desDecodeToString: function (data, key, transformation, iv) {
    var c = java.createSymmetricCrypto(transformation, key, iv);
    return c ? c.decryptStr(data) : null;
  },
  desBase64DecodeToString: function (data, key, transformation, iv) {
    // Byte-identical to the plain variant in Legado too.
    return java.desDecodeToString(data, key, transformation, iv);
  },
  desEncodeToString: function (data, key, transformation, iv) {
    // Legado: `String(createSymmetricCrypto(…).encrypt(data))` -- the raw
    // ciphertext bytes reinterpreted as text, not base64 and not hex.
    var c = java.createSymmetricCrypto(transformation, key, iv);
    return c ? c.encrypt(data).toString('utf-8') : null;
  },
  desEncodeToBase64String: function (data, key, transformation, iv) {
    var c = java.createSymmetricCrypto(transformation, key, iv);
    return c ? c.encryptBase64(data) : null;
  },
  // ---- 3DES family (JsEncodeUtils.kt:322-427) -------------------------------
  // Legado uses three different key/iv conventions across its "ArgsBase64"
  // helpers, so each one is ported as written:
  //   aesDecodeArgsBase64Str        key b64, iv b64
  //   aesEncodeArgsBase64Str        key raw, iv raw
  //   tripleDES*ArgsBase64Str       key b64, iv raw
  // Normalising them would silently break any source written against Legado.
  tripleDESDecodeStr: function (data, key, mode, padding, iv) {
    var c = java.createSymmetricCrypto('DESede/' + mode + '/' + padding, key, iv);
    return c ? c.decryptStr(data) : null;
  },
  tripleDESDecodeArgsBase64Str: function (data, key, mode, padding, iv) {
    var c = java.createSymmetricCrypto(
      'DESede/' + mode + '/' + padding,
      Buffer.from(String(key), 'base64'),
      iv
    );
    return c ? c.decryptStr(data) : null;
  },
  tripleDESEncodeBase64Str: function (data, key, mode, padding, iv) {
    var c = java.createSymmetricCrypto('DESede/' + mode + '/' + padding, key, iv);
    return c ? c.encryptBase64(data) : null;
  },
  tripleDESEncodeArgsBase64Str: function (data, key, mode, padding, iv) {
    var c = java.createSymmetricCrypto(
      'DESede/' + mode + '/' + padding,
      Buffer.from(String(key), 'base64'),
      iv
    );
    return c ? c.encryptBase64(data) : null;
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
    get: function () {
      // `|| ''` collapsed a legitimate falsy value into "": `bookSourceType` is
      // 0 for a text source and `customOrder` may be 0, so a source comparing
      // them with `===` saw "" instead of 0.  Only a genuinely absent key should
      // read as the empty string.
      return key in __nhSourceConfig ? __nhSourceConfig[key] : '';
    },
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

// Keys the Python bootstrap owns and refreshes on every evaluation.
//
// They are cleared before each merge, because the Node subprocess is
// **persistent**: a key the previous evaluation set would otherwise stay
// readable.  `chapter` is the one that actually bit -- `_build_js_context` only
// injects it when a chapter context exists, so a later chapter-less evaluation
// used to read back the *previous* chapter's title/url (the same class of bug as
// codex-handoff section 9, "上一本书的上下文串味").
//
// Variables written by `java.put` / `source.put` must NOT be listed here: those
// are the `@put`/`get` store and have to survive across evaluations.
var __nhContextKeys = [
  'baseUrl', 'bookUrl', 'sourceUrl', 'bookSourceUrl', 'url', 'book', 'chapter',
  'bookSourceName', 'bookSourceGroup', 'bookSourceType', 'bookUrlPattern',
  'customOrder', 'loginUrl', 'searchUrl', 'header',
];

function __nhDropContextKeys(target, incoming) {
  for (var i = 0; i < __nhContextKeys.length; i++) {
    var key = __nhContextKeys[i];
    if (!(key in incoming)) delete target[key];
  }
}

function __nhSetSourceConfig(cfg) {
  if (!cfg) return;
  __nhDropContextKeys(__nhSourceConfig, cfg);
  for (var k in cfg) { if (cfg[k] !== undefined) __nhSourceConfig[k] = cfg[k]; }
}
function __nhSetVars(vars) {
  if (!vars) return;
  // NB: only the context keys above are cleared.  `java.put` writes straight into
  // `__nhVars`, so a blanket reset would destroy the @put store.
  __nhDropContextKeys(__nhVars, vars);
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
