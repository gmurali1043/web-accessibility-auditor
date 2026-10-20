"""Custom Playwright/DOM heuristic checks that complement axe-core.

Most checks are read-only. The interactive checks at the end of the file
(disclosure_toggle, menu_keyboard, hover_menu_keyboard) press Enter/Space/
Arrow/Escape on expand/collapse and menu controls only, and run inside
_side_effect_guard(), which blocks page navigation, new windows and every
non-GET request while they run, so they cannot submit forms, leave the
page, or change server state. Nothing ever clicks arbitrary buttons.
Each check returns a list of issue dicts: {selector, html, message, fix}.
Some checks return an explicit "test_case_ids" key to override the default
registry-derived mapping (used when one check's findings route to
different test cases depending on what was found, e.g. inline_event_handlers).
"""

from contextlib import contextmanager

PRELUDE_JS = """
() => {
  window.__cssPath = function(el) {
    if (!(el instanceof Element)) return '';
    const path = [];
    while (el && el.nodeType === 1 && path.length < 6) {
      let selector = el.nodeName.toLowerCase();
      if (el.id) { selector += '#' + el.id; path.unshift(selector); break; }
      let sib = el, nth = 1;
      while (sib.previousElementSibling) {
        sib = sib.previousElementSibling;
        if (sib.nodeName.toLowerCase() === el.nodeName.toLowerCase()) nth++;
      }
      if (nth > 1) selector += ':nth-of-type(' + nth + ')';
      path.unshift(selector);
      el = el.parentElement;
    }
    return path.join(' > ');
  };
  window.__snippet = function(el) {
    let html = el.outerHTML || '';
    if (html.length > 300) html = html.slice(0, 300) + '\\u2026';
    return html;
  };
  window.__isShown = function(el) {
    if (!el || !el.isConnected) return false;
    if (el.checkVisibility && !el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) return false;
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1;
  };
  return true;
}
"""


def _js(page, script, *args):
    try:
        return page.evaluate(script, *args)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Pure-JS, single evaluate() checks
# ---------------------------------------------------------------------------

def check_skip_link(page):
    return _js(page, """
    () => {
      const links = Array.from(document.querySelectorAll('a[href]'));
      const candidate = links.slice(0, 2).find(a =>
        /skip\\s*(to)?\\s*(main\\s*)?content/i.test(a.textContent) ||
        /skip\\s*(to)?\\s*(main\\s*)?content/i.test(a.getAttribute('aria-label') || ''));
      if (!candidate) {
        return [{selector: 'body', html: '', message: 'No "skip to main content" link found as the first or second link on the page.', fix: 'Add a link as the first (or second) focusable element on the page, e.g. <a href="#main-content" class="skip-link">Skip to main content</a>. It may be visually hidden until focused.'}];
      }
      const href = candidate.getAttribute('href') || '';
      if (!href.startsWith('#')) {
        return [{selector: __cssPath(candidate), html: __snippet(candidate), message: 'Skip link does not use a same-page fragment href.', fix: 'Point the skip link at an in-page anchor, e.g. href="#main-content".'}];
      }
      const targetId = href.slice(1);
      if (!targetId || !document.getElementById(targetId)) {
        return [{selector: __cssPath(candidate), html: __snippet(candidate), message: `Skip link points to "#${targetId}" but no element with that id exists on the page.`, fix: `Add id="${targetId || 'main-content'}" to the main content container, or update the skip link's href to match an existing id.`}];
      }
      return [];
    }
    """)


def check_access_keys(page):
    return _js(page, """
    () => Array.from(document.querySelectorAll('[accesskey]')).map(el => ({
      selector: __cssPath(el), html: __snippet(el),
      message: `Element uses accesskey="${el.getAttribute('accesskey')}", which this requirement prohibits.`,
      fix: 'Remove the accesskey attribute and provide any keyboard shortcut through a documented, remappable mechanism instead.'
    }))
    """)


def check_invalid_href(page):
    return _js(page, r"""
    () => Array.from(document.querySelectorAll('a[href]')).filter(a => {
      const h = (a.getAttribute('href') || '').trim();
      return h === '#' || /^javascript:\s*void\(0\)\s*;?$/i.test(h) || h === 'javascript:;';
    }).map(a => ({
      selector: __cssPath(a), html: __snippet(a),
      message: `Link has a non-functional href value ("${a.getAttribute('href')}").`,
      fix: 'Use a real destination URL. If the element only triggers script behavior and never navigates, use a <button> instead of an <a>.'
    }))
    """)


def check_new_tab_links(page):
    return _js(page, """
    () => Array.from(document.querySelectorAll('a[target="_blank"]')).filter(a => {
      const label = (a.getAttribute('aria-label') || '') + ' ' + a.textContent;
      return !/new\\s*(window|tab)/i.test(label);
    }).map(a => ({
      selector: __cssPath(a), html: __snippet(a),
      message: 'Link opens in a new tab/window without an aria-label or visible text informing the user beforehand.',
      fix: 'Add wording (visually and/or via aria-label) such as "(opens in a new tab)" to the link.'
    }))
    """)


def check_target_size(page):
    return _js(page, """
    () => {
      const interactive = Array.from(document.querySelectorAll(
        'a[href], button, input:not([type=hidden]), select, textarea, [role="button"], [role="link"], [tabindex]:not([tabindex="-1"])'));
      const out = [];
      for (const el of interactive) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) continue;
        if (r.width < 24 || r.height < 24) {
          out.push({
            selector: __cssPath(el), html: __snippet(el),
            message: `Interactive element is ${Math.round(r.width)}x${Math.round(r.height)}px, below the 24x24 CSS px minimum target size.`,
            fix: "Increase the element's width/height to at least 24x24 CSS px, or confirm at least 24px of spacing to the nearest adjacent control (an accepted exception this scan does not verify automatically)."
          });
        }
      }
      return out.slice(0, 30);
    }
    """)


def check_link_text_consistency(page):
    return _js(page, """
    () => {
      const hrefToTexts = {};
      const textToHrefs = {};
      Array.from(document.querySelectorAll('a[href]')).forEach(a => {
        const href = a.href;
        const text = a.textContent.trim().replace(/\\s+/g, ' ');
        if (!text || !href) return;
        (hrefToTexts[href] = hrefToTexts[href] || new Set()).add(text);
        (textToHrefs[text] = textToHrefs[text] || new Set()).add(href);
      });
      const out = [];
      for (const [href, texts] of Object.entries(hrefToTexts)) {
        if (texts.size > 1) {
          const t0 = Array.from(texts)[0];
          const sample = Array.from(document.querySelectorAll('a[href]')).find(a => a.href === href);
          out.push({selector: sample ? __cssPath(sample) : 'a', html: sample ? __snippet(sample) : '',
            message: `Links to "${href}" use ${texts.size} different link texts: ${Array.from(texts).slice(0, 4).join(' | ')}`,
            fix: 'Use the same link text for every link that points to the same destination.'});
        }
      }
      for (const [text, hrefs] of Object.entries(textToHrefs)) {
        if (hrefs.size > 1) {
          const sample = Array.from(document.querySelectorAll('a[href]')).find(a => a.textContent.trim().replace(/\\s+/g,' ') === text);
          out.push({selector: sample ? __cssPath(sample) : 'a', html: sample ? __snippet(sample) : '',
            message: `Link text "${text}" is reused for ${hrefs.size} different destinations.`,
            fix: 'Use unique, destination-describing link text for links that go to different places.'});
        }
      }
      return out.slice(0, 30);
    }
    """)


def check_generic_link_text(page):
    return _js(page, """
    () => {
      const GENERIC = /^(click here|here|read more|more|learn more|link|this link|details|more info|continue)$/i;
      return Array.from(document.querySelectorAll('a[href]')).filter(a => GENERIC.test(a.textContent.trim())).map(a => ({
        selector: __cssPath(a), html: __snippet(a),
        message: `Link text "${a.textContent.trim()}" is not descriptive out of context.`,
        fix: 'Rewrite the link text to describe its destination or purpose, e.g. "Read the 2025 accessibility report" instead of "Read more".'
      }));
    }
    """)


def check_title_presence(page):
    return _js(page, """
    () => {
      const t = (document.title || '').trim();
      if (!t) return [{selector: 'title', html: '<title></title>', message: 'Page has no <title> text.', fix: 'Add a concise, descriptive <title> element.'}];
      if (t.length < 5) return [{selector: 'title', html: `<title>${t}</title>`, message: `Page title "${t}" is very short and may not be descriptive.`, fix: 'Expand the title to clearly describe the page purpose.'}];
      return [];
    }
    """)


def check_heading_structure(page):
    return _js(page, """
    () => {
      const out = [];
      const h1s = Array.from(document.querySelectorAll('h1'));
      if (h1s.length === 0) {
        out.push({selector: 'body', html: '', message: 'Page has no H1 element.', fix: 'Add a single H1 that describes the page topic/purpose.'});
        return out;
      }
      if (h1s.length > 1) {
        out.push({selector: __cssPath(h1s[1]), html: __snippet(h1s[1]), message: `Page has ${h1s.length} H1 elements; only one is allowed.`, fix: 'Demote extra H1s to H2 or lower, keeping a single top-level H1.'});
      }
      const h1 = h1s[0];
      const main = document.querySelector('main, [role="main"]');
      if (main) {
        if (!main.contains(h1)) {
          out.push({selector: __cssPath(h1), html: __snippet(h1), message: 'The H1 is not inside the Main landmark.', fix: 'Move the H1 inside the <main> element, at the top of its content.'});
        } else {
          let precedingText = false;
          for (const child of main.childNodes) {
            if (child === h1) break;
            if (child.nodeType === 3 && child.textContent.trim()) precedingText = true;
            if (child.nodeType === 1 && child.textContent.trim()) precedingText = true;
          }
          if (precedingText) {
            out.push({selector: __cssPath(h1), html: __snippet(h1), message: 'Content appears before the H1 inside the Main landmark.', fix: 'Move the H1 to be the first meaningful content inside <main>.'});
          }
        }
      }
      return out;
    }
    """)


def check_paragraph_markup(page):
    return _js(page, """
    () => {
      const out = [];
      document.querySelectorAll('div, span').forEach(el => {
        const brCount = (el.innerHTML.match(/<br\\s*\\/?>/gi) || []).length;
        if (brCount >= 2 && el.children.length <= brCount + 2 && el.textContent.trim().length > 120) {
          out.push({selector: __cssPath(el), html: __snippet(el), message: 'Block of text appears to use <br><br> to fake paragraph breaks instead of <p> elements.', fix: 'Wrap each paragraph of text in its own <p> element instead of separating with <br><br>.'});
        }
      });
      return out.slice(0, 15);
    }
    """)


def check_list_grouping(page):
    return _js(page, """
    () => {
      const out = [];
      const seen = new Set();
      document.querySelectorAll('nav, div, ul, ol').forEach(container => {
        if (container.closest('ul, ol, dl')) return;
        const kids = Array.from(container.children);
        const anchors = kids.filter(k => k.tagName === 'A');
        if (anchors.length >= 3 && anchors.length === kids.length && !seen.has(container)) {
          seen.add(container);
          out.push({selector: __cssPath(container), html: __snippet(container), message: `Group of ${anchors.length} sibling links is not marked up as a list.`, fix: 'Wrap the repeated items in a <ul>/<ol> with each item in an <li>.'});
        }
      });
      return out.slice(0, 15);
    }
    """)


def check_semantic_markup(page):
    return _js(page, """
    () => Array.from(document.querySelectorAll('b, i')).slice(0, 20).map(el => ({
      selector: __cssPath(el), html: __snippet(el),
      message: `<${el.tagName.toLowerCase()}> is used for visual styling; if it conveys emphasis or importance it should use semantic markup instead.`,
      fix: `Replace <${el.tagName.toLowerCase()}> with <strong> (importance) or <em> (emphasis) when the styling conveys meaning rather than pure appearance.`
    }))
    """)


def check_layout_tables(page):
    return _js(page, """
    () => Array.from(document.querySelectorAll('table')).filter(t =>
      !t.querySelector('th') && !t.querySelector('caption') &&
      t.getAttribute('role') !== 'presentation' && t.getAttribute('role') !== 'none'
    ).map(t => ({
      selector: __cssPath(t), html: __snippet(t),
      message: 'Table has no <th> or <caption>, a common signature of a layout table.',
      fix: 'If this table presents tabular data, add <th> header cells and a <caption>. If it is used purely for visual layout, replace it with CSS layout (flexbox/grid) or add role="presentation".'
    }))
    """)


def check_table_captions(page):
    return _js(page, """
    () => {
      const out = [];
      document.querySelectorAll('table').forEach(t => {
        const caption = t.querySelector('caption');
        const thCount = t.querySelectorAll('th').length;
        if (!caption || !caption.textContent.trim()) {
          out.push({selector: __cssPath(t), html: __snippet(t), message: 'Table has no <caption> identifying its purpose.', fix: 'Add a <caption> naming the table, e.g. "Transaction History for x2304" (may be visually hidden with CSS).'});
        } else if (thCount > 4 && caption.textContent.trim().length < 40) {
          out.push({selector: __cssPath(caption), html: __snippet(caption), message: `Table has ${thCount} column headers but its caption is short and may not describe the table's layout/functionality.`, fix: 'Extend the caption to describe the table structure after its title, e.g. "...: recent transactions in column 1, details in columns 2-5, actions in column 6."'});
        }
      });
      return out.slice(0, 15);
    }
    """)


def check_modal_static_aria(page):
    return _js(page, """
    () => {
      const out = [];
      document.querySelectorAll('[role="dialog"], [role="alertdialog"]').forEach(d => {
        if (d.getAttribute('aria-modal') !== 'true') {
          out.push({selector: __cssPath(d), html: __snippet(d), message: 'Dialog element is missing aria-modal="true".', fix: 'Add aria-modal="true" to the dialog container.'});
        }
        const labelledby = d.getAttribute('aria-labelledby');
        const firstId = labelledby ? labelledby.split(/\\s+/)[0] : null;
        if (!firstId || !document.getElementById(firstId)) {
          out.push({selector: __cssPath(d), html: __snippet(d), message: "Dialog element has no valid aria-labelledby pointing to a title element.", fix: 'Add aria-labelledby="<id of the dialog\\'s heading>" to the dialog container.'});
        }
        const describedby = d.getAttribute('aria-describedby');
        if (describedby) {
          const descId = describedby.split(/\\s+/)[0];
          if (!document.getElementById(descId)) {
            out.push({selector: __cssPath(d), html: __snippet(d), message: 'Dialog aria-describedby references an id that does not exist.', fix: "Point aria-describedby at an existing element containing the dialog's helper text."});
          }
        }
        const heading = d.querySelector('h1,h2,h3,h4,h5,h6');
        if (heading && heading.tagName !== 'H2') {
          out.push({selector: __cssPath(heading), html: __snippet(heading), message: `Dialog's first heading is ${heading.tagName}, not H2.`, fix: 'Use H2 for the dialog\\'s main heading.'});
        }
      });
      return out.slice(0, 20);
    }
    """)


def check_blink_flash(page):
    return _js(page, """
    () => {
      const out = [];
      document.querySelectorAll('marquee, blink').forEach(el => out.push({
        selector: __cssPath(el), html: __snippet(el),
        message: `<${el.tagName.toLowerCase()}> produces automatically moving/blinking content.`,
        fix: 'Remove the element or replace it with static content plus an explicit pause control.'
      }));
      document.querySelectorAll('*').forEach(el => {
        if (out.length > 20) return;
        const cs = getComputedStyle(el);
        if (cs.animationIterationCount === 'infinite' && cs.animationName !== 'none' && parseFloat(cs.animationDuration) > 0) {
          out.push({selector: __cssPath(el), html: __snippet(el), message: `Element has an infinitely-repeating CSS animation ("${cs.animationName}").`, fix: 'Provide a visible pause/stop control, or limit the animation to under 5 seconds / a finite iteration count.'});
        }
      });
      return out.slice(0, 20);
    }
    """)


def check_media_controls(page):
    return _js(page, """
    () => {
      const out = [];
      document.querySelectorAll('video[autoplay], audio[autoplay]').forEach(el => {
        const hasControls = el.hasAttribute('controls');
        const muted = el.hasAttribute('muted') || el.muted;
        const pauseBtn = document.querySelector('[aria-label*="pause" i], [aria-label*="stop" i]');
        if (el.tagName === 'AUDIO' && !muted && !hasControls) {
          out.push({selector: __cssPath(el), html: __snippet(el), message: 'Audio autoplays without native controls or a muted state.', fix: 'Add the controls attribute (or a custom pause/volume control), or remove autoplay/mute the element.'});
        }
        if (el.tagName === 'VIDEO' && !hasControls && !pauseBtn) {
          out.push({selector: __cssPath(el), html: __snippet(el), message: 'Video autoplays without native controls or a nearby pause control.', fix: 'Add the controls attribute or a custom control labelled to pause/stop the video.'});
        }
      });
      return out;
    }
    """)


def check_media_tracks(page):
    # Captions count if they are declared as <track>, exposed via the
    # video's textTracks (players such as hls.js/video.js add them from JS),
    # or offered by a caption toggle in a custom player. When none are found
    # up front the video is played muted for ~1.5s so JS players get a
    # chance to load their tracks, then paused and rewound.
    return _js(page, """
    async () => {
      const out = [];
      const CC_BTN = '[aria-label*="caption" i], [aria-label*="subtitle" i], [title*="caption" i], [title*="subtitle" i], [class*="caption" i][role="button"], button[class*="subtitle" i], button[class*="-cc" i]';
      const trackCaptions = v => Array.from(v.textTracks || []).some(t => t.kind === 'captions' || t.kind === 'subtitles');
      const hasCaptionsNow = v => !!v.querySelector('track[kind="captions"], track[kind="subtitles"]') || trackCaptions(v)
        || !!((v.closest('figure, [class*="player" i], [class*="video" i]') || v.parentElement || document.body).querySelector(CC_BTN));
      const videos = Array.from(document.querySelectorAll('video')).slice(0, 10);
      for (const v of videos) {
        let hasCaptions = hasCaptionsNow(v);
        if (!hasCaptions && (v.currentSrc || v.src || v.querySelector('source'))) {
          const wasPaused = v.paused, wasMuted = v.muted, t0 = v.currentTime;
          try {
            v.muted = true;
            await Promise.race([v.play(), new Promise(r => setTimeout(r, 2000))]);
            await new Promise(r => setTimeout(r, 1500));
            hasCaptions = hasCaptionsNow(v);
          } catch (e) {
          } finally {
            try { if (wasPaused) { v.pause(); v.currentTime = t0; } v.muted = wasMuted; } catch (e) {}
          }
        }
        const hasDesc = !!v.querySelector('track[kind="descriptions"]') || Array.from(v.textTracks || []).some(t => t.kind === 'descriptions');
        if (!hasCaptions) out.push({selector: __cssPath(v), html: __snippet(v), message: 'Video has no captions: no <track kind="captions">, no caption text track after briefly playing it, and no caption toggle in its player.', fix: 'Add a captions track: <track kind="captions" src="captions.vtt" srclang="en" label="English">.'});
        if (!hasDesc) out.push({selector: __cssPath(v), html: __snippet(v), message: "Video has no <track kind=\\"descriptions\\"> child; confirm whether the video conveys information visually that isn't in the audio track.", fix: 'If the video shows information not covered by the audio, add an audio-description track.'});
      }
      document.querySelectorAll('audio').forEach(a => {
        const scope = a.closest('figure, section, article') || document.body;
        if (!/transcript/i.test(scope.textContent)) {
          out.push({selector: __cssPath(a), html: __snippet(a), message: 'Audio element has no nearby text mentioning a transcript.', fix: 'Provide a text transcript of the audio content and link it near the player.'});
        }
      });
      return out.slice(0, 20);
    }
    """)


def check_tab_order(page):
    return _js(page, """
    () => Array.from(document.querySelectorAll('[tabindex]')).filter(el => parseInt(el.getAttribute('tabindex'), 10) > 0).map(el => ({
      selector: __cssPath(el), html: __snippet(el),
      message: `Element has a positive tabindex (${el.getAttribute('tabindex')}), which overrides the natural tab order.`,
      fix: 'Use tabindex="0" (or remove tabindex) and rely on DOM order for the focus sequence instead of a positive value.'
    }))
    """)


def check_keyboard_reachability(page):
    return _js(page, """
    () => {
      const interactiveTags = new Set(['A','BUTTON','INPUT','SELECT','TEXTAREA','SUMMARY']);
      const out = [];
      document.querySelectorAll('[onclick]').forEach(el => {
        if (interactiveTags.has(el.tagName)) return;
        if (!el.hasAttribute('role') || !el.hasAttribute('tabindex')) {
          out.push({selector: __cssPath(el), html: __snippet(el), message: 'Element has a click handler but is not natively focusable and lacks tabindex/role, so keyboard users likely cannot activate it.', fix: 'Use a <button>/<a> element, or add tabindex="0", an appropriate role (e.g. role="button"), and a keydown handler for Enter/Space.'});
        }
      });
      return out.slice(0, 20);
    }
    """)


def check_inline_event_handlers(page):
    raw = _js(page, """
    () => {
      const attrs = ['onfocus', 'oninput', 'onblur', 'onmousedown'];
      const out = [];
      document.querySelectorAll('*').forEach(el => {
        attrs.forEach(a => {
          if (el.hasAttribute(a)) out.push({attr: a, selector: __cssPath(el), html: __snippet(el)});
        });
      });
      return out.slice(0, 40);
    }
    """)
    routing = {
        "onfocus": (["INT-2-1"], "The onfocus attribute triggers an action directly on focus, which can surprise or disorient users.", "Remove the onfocus side-effect, or move the behavior to an explicit user-initiated control (e.g. a button)."),
        "oninput": (["INT-2-1"], "The oninput attribute triggers an action on every keystroke/change, which can surprise or disorient users.", "Remove the oninput side-effect, or move the behavior to an explicit user-initiated control (e.g. a \"Go\"/\"Apply\" button)."),
        "onblur": (["INT-2-2", "INT-2-3"], "The onblur attribute triggers an action when focus leaves the field. Prefer an explicit control unless the change is minor and clearly announced.", "Replace with an explicit \"Go\" button where possible. If an onBlur action is unavoidable, add visible warning text plus aria-describedby pointing to it before the control is reached."),
        "onmousedown": (["INT-2-4"], "The onmousedown attribute triggers an action on the pointer down-event rather than the up-event.", "Trigger the action on click (up-event) instead, or provide a way to abort/undo the action, or make the up-event reverse it."),
    }
    out = []
    for item in raw or []:
        ids, message, fix = routing.get(item["attr"], (["INT-2-1"], "Inline event handler found.", "Review this handler."))
        out.append({
            "selector": item["selector"], "html": item["html"],
            "message": message, "fix": fix,
            "test_case_ids": ids,
        })
    return out


def check_describedby_wiring(page):
    return _js(page, """
    () => Array.from(document.querySelectorAll('[aria-describedby]')).map(el => {
      const ids = el.getAttribute('aria-describedby').split(/\\s+/).filter(Boolean);
      const missing = ids.filter(id => !document.getElementById(id));
      if (missing.length) {
        return {selector: __cssPath(el), html: __snippet(el), message: `aria-describedby references missing id(s): ${missing.join(', ')}.`, fix: 'Ensure every id referenced by aria-describedby exists in the DOM.'};
      }
      return null;
    }).filter(Boolean)
    """)


def check_live_region_presence(page):
    return _js(page, """
    () => {
      const hasForm = document.querySelectorAll('form, input, textarea, select').length > 0;
      const hasLive = !!document.querySelector('[role="alert"], [role="status"], [aria-live]');
      if (hasForm && !hasLive) {
        return [{selector: 'body', html: '', message: 'Page has form controls but no role="alert"/"status" or aria-live region was found for status/error messaging.', fix: 'Wrap dynamic success/error/status messages in a container with role="alert" (or aria-live) so screen readers announce them automatically.'}];
      }
      return [];
    }
    """)


def check_visually_hidden_technique(page):
    return _js(page, """
    () => {
      const out = [];
      document.querySelectorAll('[class*="sr-only" i], [class*="visually-hidden" i], [class*="visuallyhidden" i], [class*="screen-reader" i]').forEach(el => {
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden') {
          out.push({selector: __cssPath(el), html: __snippet(el), message: 'Element named like a "screen-reader-only" helper is hidden with display:none/visibility:hidden, which also hides it from screen readers.', fix: 'Use an off-screen clipping technique (position:absolute; width:1px; height:1px; overflow:hidden;) instead of display:none/visibility:hidden.'});
        }
      });
      return out.slice(0, 15);
    }
    """)


def check_fieldset_grouping(page):
    return _js(page, """
    () => {
      const out = [];
      const byName = {};
      document.querySelectorAll('input[type=radio][name], input[type=checkbox][name]').forEach(el => {
        (byName[el.name] = byName[el.name] || []).push(el);
      });
      for (const [name, els] of Object.entries(byName)) {
        if (els.length < 2) continue;
        const grouped = els.every(el => el.closest('fieldset') || el.closest('[role="group"], [role="radiogroup"]'));
        if (!grouped) {
          out.push({selector: __cssPath(els[0]), html: __snippet(els[0]), message: `Group of ${els.length} related controls (name="${name}") is not wrapped in a <fieldset> or ARIA group.`, fix: 'Wrap the related controls in <fieldset><legend>...</legend>...</fieldset>, or add role="group"/"radiogroup" with an aria-label.'});
        }
      }
      return out.slice(0, 15);
    }
    """)


def check_required_fields(page):
    return _js(page, """
    () => Array.from(document.querySelectorAll('input, select, textarea')).filter(el =>
      el.hasAttribute('required') && el.getAttribute('aria-required') !== 'true'
    ).map(el => ({
      selector: __cssPath(el), html: __snippet(el),
      message: 'Field has the HTML required attribute but no aria-required="true".',
      fix: 'Add aria-required="true" alongside the required attribute for broader assistive technology support.'
    }))
    """)


def check_autocomplete_purpose(page):
    return _js(page, r"""
    () => {
      const out = [];
      const map = [[/e-?mail/i,'email'],[/first.?name/i,'given-name'],[/last.?name/i,'family-name'],
                   [/phone|tel/i,'tel'],[/postal|zip/i,'postal-code'],[/address/i,'street-address'],[/country/i,'country']];
      document.querySelectorAll('input').forEach(el => {
        if (el.type === 'hidden') return;
        const label = document.querySelector(`label[for="${el.id}"]`);
        const hint = (el.name || '') + ' ' + (el.id || '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (label ? label.textContent : '');
        for (const [re, token] of map) {
          if (re.test(hint)) {
            const ac = el.getAttribute('autocomplete');
            if (!ac || !ac.includes(token)) {
              out.push({selector: __cssPath(el), html: __snippet(el), message: `Field appears to collect ${token.replace('-', ' ')} but autocomplete="${ac || ''}" doesn't match.`, fix: `Add autocomplete="${token}" to help browsers and password managers autofill this field correctly.`});
            }
            break;
          }
        }
      });
      return out.slice(0, 20);
    }
    """)


def check_auth_field_methods(page):
    return _js(page, """
    () => {
      const out = [];
      document.querySelectorAll('input[type=password]').forEach(el => {
        const ac = el.getAttribute('autocomplete');
        if (!ac) {
          out.push({selector: __cssPath(el), html: __snippet(el), message: 'Password field has no autocomplete attribute, which can prevent password-manager autofill.', fix: 'Add autocomplete="current-password" (login) or "new-password" (signup/reset).'});
        }
        if (el.hasAttribute('onpaste')) {
          out.push({selector: __cssPath(el), html: __snippet(el), message: 'Password field has an onpaste handler, which often blocks pasting credentials.', fix: 'Remove any paste-blocking handler so users can paste from a password manager.'});
        }
      });
      return out.slice(0, 15);
    }
    """)


def check_form_structure(page):
    return _js(page, """
    () => {
      const out = [];
      document.querySelectorAll('form').forEach(form => {
        const required = form.querySelectorAll('[required], [aria-required="true"]');
        if (required.length === 0) return;
        const hasNovalidate = form.hasAttribute('novalidate');
        const errorContainer = form.querySelector('[role="alert"], [aria-live], [class*="error" i]');
        if (hasNovalidate && !errorContainer) {
          out.push({selector: __cssPath(form), html: __snippet(form), message: 'Form disables native validation (novalidate) but has no visible error-message container for custom validation.', fix: 'Ensure custom client-side validation displays errors in a container with role="alert" or aria-live, tied to each field via aria-describedby.'});
        }
        if (!errorContainer) {
          out.push({selector: __cssPath(form), html: __snippet(form), message: 'Form with required fields has no detectable error-message container (role="alert"/aria-live or an element with an "error" class) wired up for validation feedback.', fix: 'Add an error-summary or per-field error container using role="alert" (or aria-live), referenced via aria-describedby.'});
        }
      });
      return out.slice(0, 10);
    }
    """)


def check_role_semantics(page):
    return _js(page, """
    () => {
      const out = [];
      document.querySelectorAll('a:not([href])').forEach(el => {
        if (el.hasAttribute('onclick')) {
          out.push({selector: __cssPath(el), html: __snippet(el), message: 'Anchor element has no href and is being used to trigger an action; it should likely be a <button>.', fix: 'Use a <button> element for actions, and reserve <a href="...​"> for navigation.'});
        }
      });
      document.querySelectorAll('div[onclick], span[onclick]').forEach(el => {
        if (!el.hasAttribute('role')) {
          out.push({selector: __cssPath(el), html: __snippet(el), message: `<${el.tagName.toLowerCase()}> has a click handler but no role, so its purpose is not exposed to assistive technology.`, fix: 'Use a native <button>/<a> element, or add role="button"/"link" plus keyboard support.'});
        }
      });
      return out.slice(0, 20);
    }
    """)


def check_pagination(page):
    return _js(page, """
    () => {
      const out = [];
      const candidates = new Set();
      document.querySelectorAll('[class*="pagin" i], [id*="pagin" i], nav').forEach(el => candidates.add(el));
      candidates.forEach(el => {
        const looksLikePagination = /pagin/i.test(el.className || '') || /pagin/i.test(el.id || '') ||
          (el.querySelectorAll('a').length >= 3 && /next|previous|prev/i.test(el.textContent));
        if (!looksLikePagination) return;
        if (el.getAttribute('role') !== 'navigation' && el.tagName !== 'NAV') {
          out.push({selector: __cssPath(el), html: __snippet(el), message: 'Likely pagination control does not have role="navigation".', fix: 'Add role="navigation" (or use a <nav> element) on the pagination container.'});
        }
        const label = el.getAttribute('aria-label') || '';
        if (!/pagination/i.test(label)) {
          out.push({selector: __cssPath(el), html: __snippet(el), message: 'Likely pagination control has no aria-label containing "Pagination".', fix: 'Add aria-label="Pagination" (or a unique label containing the word "Pagination" if more than one pagination control is present).'});
        }
      });
      return out.slice(0, 10);
    }
    """)


def check_find_content_methods(page):
    return _js(page, """
    () => {
      const hasNav = !!document.querySelector('nav, [role="navigation"]');
      const hasSearch = !!document.querySelector('input[type="search"], [role="search"], form[role="search"]');
      const hasSitemapLink = Array.from(document.querySelectorAll('a')).some(a => /sitemap/i.test(a.textContent) || /sitemap/i.test(a.href || ''));
      const methods = [hasNav, hasSearch, hasSitemapLink].filter(Boolean).length;
      if (methods < 2) {
        return [{selector: 'body', html: '', message: `Only ${methods} way(s) to find content were automatically detected (navigation menu, search, sitemap link).`, fix: 'Provide at least two ways to locate content: e.g. a navigation menu plus a search function, or a sitemap/table of contents.'}];
      }
      return [];
    }
    """)


def check_wizard_context(page):
    return _js(page, """
    () => {
      const bodyText = document.body.innerText || '';
      const m = bodyText.match(/step\\s+\\d+\\s+of\\s+\\d+/i);
      if (!m) return [];
      const stepText = m[0];
      const inTitle = document.title.toLowerCase().includes(stepText.toLowerCase());
      const h1 = document.querySelector('h1');
      const inH1 = h1 && h1.textContent.toLowerCase().includes(stepText.toLowerCase());
      if (!inTitle && !inH1) {
        return [{selector: h1 ? __cssPath(h1) : 'title', html: h1 ? __snippet(h1) : `<title>${document.title}</title>`, message: `Found step indicator text "${stepText}" but it does not appear in the page <title> or H1.`, fix: `Include the step indicator text (e.g. "${stepText}") in both the page title and the H1.`}];
      }
      return [];
    }
    """)


def check_reading_order(page):
    return _js(page, """
    () => {
      const blocks = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6,p,li,td,th'))
        .filter(el => el.textContent.trim().length > 0);
      const withRects = blocks.map((el, i) => ({el, domIndex: i, rect: el.getBoundingClientRect()}))
        .filter(o => o.rect.width > 0 && o.rect.height > 0);
      if (withRects.length < 5) return [];
      const visualOrder = [...withRects].sort((a, b) => (a.rect.top - b.rect.top) || (a.rect.left - b.rect.left));
      let mismatches = 0;
      for (let i = 0; i < visualOrder.length; i++) {
        if (visualOrder[i].domIndex !== withRects[i].domIndex) mismatches++;
      }
      if (mismatches > Math.max(3, withRects.length * 0.15)) {
        return [{selector: 'body', html: '', message: `Visual (top-to-bottom) reading order diverges from DOM order for ${mismatches} of ${withRects.length} text blocks, which can confuse screen reader and linearized reading order.`, fix: 'Reorder the HTML so the source/DOM order matches the intended visual reading order, and avoid CSS that only reorders content visually (e.g. the "order" property).'}];
      }
      return [];
    }
    """)


def check_parked_content(page):
    return _js(page, """
    () => {
      const out = [];
      const bodyChildren = Array.from(document.body.querySelectorAll('*'));
      const total = bodyChildren.length || 1;
      bodyChildren.forEach((el, idx) => {
        const cs = getComputedStyle(el);
        const hidden = (cs.display === 'none' || cs.visibility === 'hidden');
        if (hidden && !el.hasAttribute('aria-hidden') && el.textContent.trim().length > 30 && idx < total * 0.7) {
          out.push({selector: __cssPath(el), html: __snippet(el), message: 'Hidden "parked" content is not located near the bottom of the document.', fix: 'Move content that is hidden and only used/displayed later (e.g. by a JS date picker) to just before </body>, or remove it from the DOM until needed.'});
        }
      });
      return out.slice(0, 10);
    }
    """)


# Finds CSS rules of the form "<host>:hover <combinator> <target>" whose
# declarations *reveal* the target (display/visibility/opacity), then pairs
# each rule with up to 3 live host elements that currently hide a matching
# target and contain something keyboard-focusable. Purely cosmetic :hover
# rules (colour, underline, shadow...) and native title="" tooltips (exempt
# from WCAG 1.4.13 as user-agent controlled) are ignored. Nav <li> menus are
# left to hover_menu_keyboard, which tests them with a real mouse hover.
HOVER_REVEAL_CANDIDATES_JS = r"""
([attr, maxRules]) => {
  const splitList = (text) => {
    const parts = []; let depth = 0, cur = '';
    for (const ch of text) {
      if (ch === '(') depth++;
      if (ch === ')') depth--;
      if (ch === ',' && depth === 0) { parts.push(cur.trim()); cur = ''; } else cur += ch;
    }
    if (cur.trim()) parts.push(cur.trim());
    return parts;
  };
  const reveals = (st) =>
    (st.display && st.display !== 'none') || st.visibility === 'visible' ||
    (st.opacity !== '' && parseFloat(st.opacity) > 0) || (st.maxHeight && st.maxHeight !== '0' && st.maxHeight !== '0px');
  const FOCUSABLE = 'a[href], button, input, select, textarea, summary, [tabindex]:not([tabindex="-1"])';
  const rules = [];
  const walk = (list) => {
    for (const rule of list) {
      if (rule.cssRules && !rule.selectorText) { try { walk(rule.cssRules); } catch (e) {} continue; }
      if (rule.selectorText && rule.style) rules.push(rule);
    }
  };
  for (const sheet of document.styleSheets) { try { walk(sheet.cssRules); } catch (e) {} }

  const out = []; const seenHosts = new Set(); let n = 0;
  for (const rule of rules) {
    if (out.length >= maxRules) break;
    if (!rule.selectorText.includes(':hover') || !reveals(rule.style)) continue;
    for (const sel of splitList(rule.selectorText)) {
      const m = sel.match(/^(.*?):hover((?:[:\[][^\s>+~]*)?)(\s*[\s>+~]\s*.+)$/);
      if (!m) continue;                                  // :hover styles the element itself
      const hostSel = (m[1] + m[2]).trim() || '*';
      const targetSel = sel.replace(/:hover/g, '');
      let hosts, targets;
      try { hosts = Array.from(document.querySelectorAll(hostSel)).slice(0, 200); targets = Array.from(document.querySelectorAll(targetSel)); } catch (e) { continue; }
      for (const host of hosts) {
        if (out.length >= maxRules) break;
        if (seenHosts.has(host)) continue;
        if (host.matches('nav li, [role="navigation"] li, header li')) continue;
        if (!__isShown(host)) continue;
        const target = targets.find(t => !__isShown(t) && (host.contains(t) || (host.parentElement && host.parentElement.contains(t))));
        if (!target) continue;
        const focusTarget = host.matches(FOCUSABLE) ? host : host.querySelector(FOCUSABLE);
        if (!focusTarget || !__isShown(focusTarget)) continue;
        seenHosts.add(host);
        const id = 'a11y-hv' + (n++);
        focusTarget.setAttribute(attr, id);
        target.setAttribute(attr, id + '-t');
        out.push({id, rule: sel, selector: __cssPath(host), html: __snippet(host)});
      }
    }
  }
  return out;
}
"""


def check_hover_focus_parity(page):
    issues = []
    try:
        _ensure_prelude(page)
        candidates = page.evaluate(HOVER_REVEAL_CANDIDATES_JS, [AUDIT_ATTR, 10])
    except Exception:
        return []
    try:
        for c in candidates:
            _focus(page, c["id"])
            page.wait_for_timeout(350)
            shown_on_focus = _state(page, c["id"]).get("shown")
            page.evaluate("() => document.activeElement && document.activeElement.blur && document.activeElement.blur()")
            if not shown_on_focus:
                issues.append({
                    "selector": c["selector"], "html": c["html"],
                    "message": f'Content revealed by the CSS rule "{c["rule"]}" appears on mouse hover, but stayed hidden when the element was focused with the keyboard.',
                    "fix": "Add a matching :focus-within (or :focus-visible) rule, e.g. duplicate the selector with :focus-within in place of :hover, so keyboard users see the same content.",
                })
    finally:
        try:
            page.evaluate("""(attr) => document.querySelectorAll('[' + attr + ']').forEach(el => el.removeAttribute(attr))""", AUDIT_ATTR)
        except Exception:
            pass
    return issues


def check_disabled_focus_static(page):
    return _js(page, """
    () => Array.from(document.querySelectorAll('[aria-disabled="true"]')).filter(el => {
      const tabindex = el.getAttribute('tabindex');
      const nativelyFocusable = ['A','BUTTON','INPUT','SELECT','TEXTAREA'].includes(el.tagName);
      return (tabindex && tabindex !== '-1') || (nativelyFocusable && !el.disabled);
    }).map(el => ({
      selector: __cssPath(el), html: __snippet(el),
      message: 'Element is marked aria-disabled="true" but remains focusable (no tabindex="-1" and/or missing the native disabled attribute).',
      fix: 'Set tabindex="-1" (for custom widgets) or use the native disabled attribute so the control is removed from the tab order, unless it is a listbox option, menu item, tab, or tree item.'
    }))
    """)


# ---------------------------------------------------------------------------
# Interaction-based checks (keyboard only - no clicks, no form submission)
# ---------------------------------------------------------------------------

def check_focus_visibility(page):
    issues = []
    try:
        count = page.evaluate(
            "() => document.querySelectorAll('a[href],button,input,select,textarea,[tabindex]:not([tabindex=\"-1\"])').length"
        )
        n = min(count, 30)
        page.evaluate("() => document.body.setAttribute('tabindex','-1')")
        page.evaluate("() => document.body.focus()")
        for _ in range(n):
            page.keyboard.press("Tab")
            info = page.evaluate("""() => {
                const el = document.activeElement;
                if (!el || el === document.body) return null;
                const cs = getComputedStyle(el);
                const outlineNone = (cs.outlineStyle === 'none' || cs.outlineWidth === '0px');
                const hasShadow = cs.boxShadow && cs.boxShadow !== 'none';
                return {selector: __cssPath(el), html: __snippet(el), outlineNone, hasShadow};
            }""")
            if info and info["outlineNone"] and not info["hasShadow"]:
                issues.append({
                    "selector": info["selector"], "html": info["html"],
                    "message": "Focused element suppresses the default focus outline (outline: none) with no visible replacement indicator (e.g. box-shadow).",
                    "fix": "Never remove the focus outline without providing a clearly visible replacement, such as a high-contrast outline or box-shadow, on :focus-visible.",
                })
        page.evaluate("() => document.body.removeAttribute('tabindex')")
    except Exception:
        pass
    # de-dupe by selector, cap
    seen = set()
    deduped = []
    for i in issues:
        if i["selector"] in seen:
            continue
        seen.add(i["selector"])
        deduped.append(i)
    return deduped[:15]


def check_keyboard_trap(page):
    issues = []
    try:
        page.evaluate("() => document.body.setAttribute('tabindex','-1')")
        page.evaluate("() => document.body.focus()")
        last = None
        stuck = 0
        for _ in range(60):
            page.keyboard.press("Tab")
            sel = page.evaluate("() => { const el = document.activeElement; return el ? __cssPath(el) : null; }")
            if sel is not None and sel == last:
                stuck += 1
                if stuck >= 6:
                    info = page.evaluate("() => { const el = document.activeElement; return el ? __snippet(el) : ''; }")
                    issues.append({
                        "selector": sel, "html": info,
                        "message": "Keyboard focus appears to stay on the same element for many consecutive Tab presses, a possible keyboard trap.",
                        "fix": "Confirm the user can Tab/Shift+Tab away from this element and, if it is a custom widget (e.g. a modal), that it returns focus to the launching element when dismissed.",
                    })
                    break
            else:
                stuck = 0
            last = sel
        page.evaluate("() => document.body.removeAttribute('tabindex')")
    except Exception:
        pass
    return issues


def check_reflow(page):
    issues = []
    orig = page.viewport_size
    try:
        page.set_viewport_size({"width": 320, "height": 800})
        page.wait_for_timeout(150)
        h_overflow = page.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 5")
        if h_overflow:
            issues.append({
                "selector": "html", "html": "",
                "message": "At a 320 CSS px wide viewport, the page requires horizontal scrolling to view content.",
                "fix": "Ensure the layout reflows to a single column at narrow widths so only vertical scrolling is needed (tables, maps, and similarly two-dimensional content are exempt).",
            })
        page.set_viewport_size({"width": 800, "height": 256})
        page.wait_for_timeout(150)
        v_overflow = page.evaluate("() => document.documentElement.scrollHeight > document.documentElement.clientHeight + 5")
        if v_overflow:
            issues.append({
                "selector": "html", "html": "",
                "message": "At a 256 CSS px tall viewport, the page requires vertical scrolling to view content.",
                "fix": "Ensure the layout reflows so only horizontal scrolling is needed at very short viewport heights (tables, maps, and similarly two-dimensional content are exempt).",
            })
    except Exception:
        pass
    finally:
        if orig:
            page.set_viewport_size(orig)
    return issues


def check_orientation(page):
    issues = []
    orig = page.viewport_size or {"width": 1280, "height": 800}
    try:
        page.set_viewport_size({"width": 400, "height": 800})
        page.wait_for_timeout(120)
        portrait_overflow = page.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 5")
        page.set_viewport_size({"width": 800, "height": 400})
        page.wait_for_timeout(120)
        landscape_overflow = page.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 5")
        if portrait_overflow or landscape_overflow:
            which = " and ".join([w for w, flag in [("portrait", portrait_overflow), ("landscape", landscape_overflow)] if flag])
            issues.append({
                "selector": "html", "html": "",
                "message": f"Content overflows horizontally when rendered in {which} dimensions.",
                "fix": "Ensure the layout adapts responsively to both portrait and landscape orientations without introducing horizontal scrolling, unless a specific orientation is essential to the content.",
            })
    except Exception:
        pass
    finally:
        page.set_viewport_size(orig)
    return issues


def check_zoom_resize(page):
    issues = []
    try:
        page.evaluate("() => { document.documentElement.style.fontSize = '200%'; }")
        page.wait_for_timeout(150)
        overflow = page.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 10")
        clipped = page.evaluate("""() => {
            let count = 0;
            const els = document.querySelectorAll('*');
            for (const el of els) {
                const cs = getComputedStyle(el);
                if (cs.overflow === 'hidden' && el.scrollHeight > el.clientHeight + 5 && el.textContent.trim().length > 0) {
                    count++;
                    if (count > 3) break;
                }
            }
            return count;
        }""")
        if overflow or clipped:
            issues.append({
                "selector": "html", "html": "",
                "message": "Increasing text size to 200% causes horizontal overflow or clipped/truncated content.",
                "fix": "Use relative units (em/rem/%) and avoid fixed-height containers with overflow:hidden so text can reflow at larger sizes without clipping.",
            })
    except Exception:
        pass
    finally:
        try:
            page.evaluate("() => { document.documentElement.style.fontSize = ''; }")
        except Exception:
            pass
    return issues


def check_text_spacing(page):
    issues = []
    css = """
    * { line-height: 1.5 !important; letter-spacing: 0.12em !important; word-spacing: 0.16em !important; }
    p { margin-bottom: 2em !important; }
    """
    handle = None
    try:
        handle = page.add_style_tag(content=css)
        page.wait_for_timeout(150)
        overflow = page.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 10")
        clipped = page.evaluate("""() => {
            let count = 0;
            document.querySelectorAll('*').forEach(el => {
                const cs = getComputedStyle(el);
                if ((cs.overflow === 'hidden' || cs.textOverflow === 'ellipsis') && el.scrollHeight > el.clientHeight + 5) count++;
            });
            return count;
        }""")
        if overflow or clipped > 2:
            issues.append({
                "selector": "html", "html": "",
                "message": "Applying the WCAG 1.4.12 text-spacing values (line-height 1.5x, paragraph spacing 2x, letter-spacing 0.12x, word-spacing 0.16x) causes overflow or clipped content.",
                "fix": "Use flexible sizing (min-height rather than fixed height with overflow:hidden) so increased line-height/letter-spacing/word-spacing doesn't clip text.",
            })
    except Exception:
        pass
    finally:
        if handle is not None:
            try:
                handle.evaluate("(el) => el.remove()")
            except Exception:
                pass
    return issues


# ---------------------------------------------------------------------------
# Embedded video players (YouTube / Vimeo). Read-only: inspects the player
# iframe's own state; never presses play.
# ---------------------------------------------------------------------------

EMBED_PROVIDERS = (
    ("YouTube", ("youtube.com/embed/", "youtube-nocookie.com/embed/")),
    ("Vimeo", ("player.vimeo.com/video/",)),
)

# The embed player only loads its caption list once playback starts, so the
# video is started muted, read, then stopped (back to its thumbnail).
YOUTUBE_CAPTIONS_JS = """
async () => {
  const p = document.getElementById('movie_player');
  const read = () => {
    try {
      const r = p && p.getPlayerResponse && p.getPlayerResponse();
      if (r && r.videoDetails) {
        const tracks = (((r.captions || {}).playerCaptionsTracklistRenderer || {}).captionTracks) || [];
        return {known: true, count: tracks.length, asrOnly: tracks.length > 0 && tracks.every(t => t.kind === 'asr')};
      }
    } catch (e) {}
    return null;
  };
  let res = read();
  if (!res && p && p.playVideo) {
    const wasMuted = p.isMuted ? p.isMuted() : false;
    try {
      p.mute();
      p.playVideo();
      for (let i = 0; i < 30 && !res; i++) {
        await new Promise(r => setTimeout(r, 200));
        res = read();
      }
    } catch (e) {
    } finally {
      try { p.stopVideo(); if (!wasMuted) p.unMute(); } catch (e) {}
    }
  }
  if (res) return res;
  const b = document.querySelector('.ytp-subtitles-button');
  if (b) {
    const label = b.getAttribute('aria-label') || b.getAttribute('title') || b.getAttribute('data-title-no-tooltip') || '';
    if (/unavailable/i.test(label)) return {known: true, count: 0, asrOnly: false};
    if (getComputedStyle(b).display !== 'none') return {known: true, count: 1, asrOnly: false};
  }
  return {known: false};
}
"""

VIMEO_CAPTIONS_JS = """
() => {
  try {
    const c = window.playerConfig;
    if (c && c.request) {
      const tracks = c.request.text_tracks || [];
      return {known: true, count: tracks.length, asrOnly: false};
    }
  } catch (e) {}
  return {known: false};
}
"""


def _embed_provider(url):
    for name, patterns in EMBED_PROVIDERS:
        if any(p in (url or "") for p in patterns):
            return name
    return None


def check_media_embeds(page):
    """Reports YouTube/Vimeo embeds whose player says it has no captions (or
    only auto-generated ones). Embeds whose caption state can't be read
    (lazy/blocked player, consent wall, other providers) raise nothing and
    stay in manual review."""
    issues = []
    try:
        count = page.evaluate("""() => {
            const frames = Array.from(document.querySelectorAll('iframe')).filter(f => /youtube(-nocookie)?\\.com\\/embed\\/|player\\.vimeo\\.com\\/video\\//.test(f.src || f.getAttribute('data-src') || ''));
            frames.slice(0, 10).forEach(f => { f.loading = 'eager'; f.scrollIntoView({block: 'center'}); });
            return frames.length;
        }""")
        if not count:
            return []
        page.wait_for_timeout(1500)
        page.evaluate("() => window.scrollTo(0, 0)")
        for frame in page.frames:
            provider = _embed_provider(frame.url)
            if not provider:
                continue
            script = YOUTUBE_CAPTIONS_JS if provider == "YouTube" else VIMEO_CAPTIONS_JS
            try:
                frame.wait_for_function("() => document.readyState === 'complete'", timeout=5000)
                if provider == "YouTube":
                    frame.wait_for_function("() => { const p = document.getElementById('movie_player'); return p && p.playVideo; }", timeout=5000)
            except Exception:
                pass
            try:
                status = frame.evaluate(script)
            except Exception:
                continue
            if not status or not status.get("known"):
                continue
            try:
                el = frame.frame_element()
                where = page.evaluate("(f) => ({selector: __cssPath(f), html: __snippet(f)})", el)
            except Exception:
                where = {"selector": "iframe", "html": frame.url}
            if status.get("count", 0) == 0:
                issues.append({
                    **where,
                    "message": f"Embedded {provider} video has no captions available in its player.",
                    "fix": f"Upload a caption file (e.g. .vtt/.srt) for this video in {provider}, or publish a version with open captions.",
                })
            elif status.get("asrOnly"):
                issues.append({
                    **where,
                    "message": f"Embedded {provider} video only has auto-generated captions, which are usually not accurate enough to meet the captions requirement.",
                    "fix": f"Upload reviewed/edited captions for this video in {provider} instead of relying on auto-generated ones.",
                })
    except Exception:
        pass
    return issues


# ---------------------------------------------------------------------------
# Interactive widget checks: activate expand/collapse and menu controls with
# the keyboard and verify the resulting state. Always run inside
# _side_effect_guard().
# ---------------------------------------------------------------------------

AUDIT_ATTR = "data-a11y-audit"
STATE_WAIT_MS = 800
MAX_DISCLOSURES = 12
MAX_MENUS = 8
MAX_HOVER_MENUS = 8


@contextmanager
def _side_effect_guard(page):
    """While active: aborts every non-GET/HEAD request, every top-level
    navigation (this page or a popup), and stubs window.open. On exit it
    closes any popup, removes audit markers, and reloads the original URL if
    the page somehow moved (e.g. a client-side router's pushState)."""
    context = page.context
    start_url = page.url
    pages_before = set(context.pages)

    def handler(route):
        req = route.request
        try:
            top_level_nav = req.is_navigation_request() and req.frame.parent_frame is None
        except Exception:
            top_level_nav = False
        if top_level_nav or req.method not in ("GET", "HEAD"):
            route.abort("aborted")
        else:
            route.continue_()

    context.route("**/*", handler)
    try:
        page.evaluate("() => { window.__a11yOrigOpen = window.open; window.open = () => null; }")
    except Exception:
        pass
    try:
        yield
    finally:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        try:
            context.unroute("**/*", handler)
        except Exception:
            pass
        for p in context.pages:
            if p not in pages_before:
                try:
                    p.close()
                except Exception:
                    pass
        if page.url.split("#")[0] != start_url.split("#")[0]:
            try:
                page.goto(start_url, wait_until="load", timeout=30000)
            except Exception:
                pass
            _ensure_prelude(page)
        try:
            page.evaluate("""(attr) => {
                if (window.__a11yOrigOpen) window.open = window.__a11yOrigOpen;
                document.querySelectorAll('[' + attr + ']').forEach(el => el.removeAttribute(attr));
                if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
            }""", AUDIT_ATTR)
        except Exception:
            pass


def _ensure_prelude(page):
    try:
        if not page.evaluate("() => typeof window.__isShown === 'function'"):
            page.evaluate(PRELUDE_JS)
    except Exception:
        pass


def _guarded(fn):
    def run(page):
        _ensure_prelude(page)
        with _side_effect_guard(page):
            return fn(page)
    run.__name__ = fn.__name__
    run.__doc__ = fn.__doc__
    return run


TOGGLE_CANDIDATES_JS = """
([attr, maxDisclosures, maxMenus]) => {
  const SKIP_ROLES = new Set(['combobox', 'tab', 'treeitem', 'option', 'row', 'gridcell', 'listbox', 'textbox', 'searchbox',
                              'slider', 'spinbutton', 'switch', 'checkbox', 'radio', 'menuitemcheckbox', 'menuitemradio']);
  const NAV_REGION = 'nav, [role="navigation"], header, [role="banner"], [role="menubar"]';
  const out = [];
  const usedTargets = new Set();
  let n = 0, disclosures = 0, menus = 0;
  const els = document.querySelectorAll('[aria-expanded], [aria-haspopup], button[aria-controls], [role="button"][aria-controls]');
  for (const el of els) {
    if (['INPUT', 'SELECT', 'TEXTAREA', 'OPTION'].includes(el.tagName)) continue;
    const role = (el.getAttribute('role') || '').toLowerCase();
    if (SKIP_ROLES.has(role)) continue;
    const popup = (el.getAttribute('aria-haspopup') || '').toLowerCase();
    if (['dialog', 'listbox', 'grid', 'tree', 'false'].includes(popup)) continue;
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
    if (el.tagName === 'BUTTON' && el.type === 'submit' && el.form) continue;
    if (!__isShown(el)) continue;
    const hasExpanded = el.hasAttribute('aria-expanded');
    if (hasExpanded && el.getAttribute('aria-expanded') === 'true') continue;  // only test closed -> open -> closed
    const href = el.tagName === 'A' ? (el.getAttribute('href') || '') : '';
    const realLink = el.tagName === 'A' && !!href && !href.startsWith('#') && !/^javascript:/i.test(href);
    const declaredMenu = popup === 'true' || popup === 'menu';
    const isMenu = declaredMenu || (hasExpanded && !!el.closest(NAV_REGION));
    if (realLink && !isMenu) continue;
    if (isMenu ? menus >= maxMenus : disclosures >= maxDisclosures) continue;

    let target = null, targetKnown = false;
    const ctl = (el.getAttribute('aria-controls') || '').trim().split(/\\s+/)[0];
    if (ctl && document.getElementById(ctl)) { target = document.getElementById(ctl); targetKnown = true; }
    else if (isMenu) {
      const li = el.closest('li');
      target = (li && li.querySelector(':scope > ul, :scope > ol, :scope > [role="menu"], :scope > div')) || el.nextElementSibling;
    } else {
      target = el.nextElementSibling || (el.parentElement && el.parentElement.nextElementSibling);
    }
    if (target && (target === el || target.contains(el) || target === document.body)) target = null;
    if (target && usedTargets.has(target)) continue;
    if (target) usedTargets.add(target);
    if (!hasExpanded && !(target && targetKnown)) continue;  // nothing reliable to verify

    const id = 'a11y-' + (n++);
    el.setAttribute(attr, id);
    if (target) target.setAttribute(attr, id + '-t');
    if (isMenu) menus++; else disclosures++;
    out.push({
      id, kind: isMenu ? 'menu' : 'disclosure', declaredMenu, realLink, hasExpanded, targetKnown,
      hasTarget: !!target, targetRole: target ? (target.getAttribute('role') || '') : '',
      selector: __cssPath(el), html: __snippet(el),
    });
  }
  return out;
}
"""

TOGGLE_STATE_JS = """
([attr, id]) => {
  const el = document.querySelector('[' + attr + '="' + id + '"]');
  const t = document.querySelector('[' + attr + '="' + id + '-t"]');
  const a = document.activeElement;
  const focusables = t ? Array.from(t.querySelectorAll('a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])')).filter(__isShown) : [];
  return {
    exists: !!el,
    expanded: el ? el.getAttribute('aria-expanded') : null,
    shown: t ? __isShown(t) : null,
    focusOnEl: !!el && a === el,
    focusInTarget: !!(t && a && a !== document.body && t.contains(a)),
    targetFocusables: focusables.length,
  };
}
"""


def _state(page, cid):
    try:
        return page.evaluate(TOGGLE_STATE_JS, [AUDIT_ATTR, cid])
    except Exception:
        return {"exists": False}


def _changed(before, after):
    return before.get("expanded") != after.get("expanded") or (
        before.get("shown") is not None and before.get("shown") != after.get("shown"))


def _wait_for_change(page, cid, before):
    after = _state(page, cid)
    waited = 0
    while after.get("exists") and not _changed(before, after) and waited < STATE_WAIT_MS:
        page.wait_for_timeout(100)
        waited += 100
        after = _state(page, cid)
    if after.get("exists") and _changed(before, after):
        page.wait_for_timeout(250)  # let open/close animations settle
        after = _state(page, cid)
    return after


def _focus(page, cid):
    try:
        return page.evaluate("""([attr, id]) => {
            const el = document.querySelector('[' + attr + '="' + id + '"]');
            if (!el) return false;
            el.focus({preventScroll: false});
            return document.activeElement === el;
        }""", [AUDIT_ATTR, cid])
    except Exception:
        return False


def _activate(page, cid, keys):
    """Focuses the control and tries each key in turn until its state
    changes. Returns (key_that_worked_or_None, before_state, after_state)."""
    before = _state(page, cid)
    after = before
    for key in keys:
        if not _focus(page, cid):
            break
        page.keyboard.press(key)
        after = _wait_for_change(page, cid, before)
        if not after.get("exists") or _changed(before, after):
            return key, before, after
    return None, before, after


def _restore(page, cid, key, original):
    """Returns the control to its original (collapsed) state."""
    for k in ([key] if key else []) + ["Escape"]:
        cur = _state(page, cid)
        if not cur.get("exists") or not _changed(original, cur):
            return
        _focus(page, cid)
        page.keyboard.press(k)
        _wait_for_change(page, cid, cur)


def _issue(c, tc_ids, message, fix):
    return {"selector": c["selector"], "html": c["html"], "message": message, "fix": fix, "test_case_ids": tc_ids}


def _aria_state_issues(c, before, after, what):
    """aria-expanded problems once the control is known to have toggled its
    content. Returns a list of issues (usually 0 or 1)."""
    if not c["hasExpanded"]:
        return [_issue(c, ["INT-5-7"],
                       f"This {what} shows/hides content when activated but has no aria-expanded attribute, so screen reader users are not told whether it is open or closed.",
                       'Add aria-expanded="false" to the control and set it to "true" while the content is shown (plus aria-controls pointing to the content).')]
    if after.get("expanded") == before.get("expanded"):
        return [_issue(c, ["INT-5-7"],
                       f'This {what} opened its content, but aria-expanded stayed "{before.get("expanded")}".',
                       'Update aria-expanded to "true" when the content is shown and back to "false" when it is hidden.')]
    if c["targetKnown"] and after.get("shown") is not None and (after.get("expanded") == "true") != after.get("shown"):
        state = "visible" if after.get("shown") else "hidden"
        return [_issue(c, ["INT-5-7"],
                       f'After activation this {what} reports aria-expanded="{after.get("expanded")}" but the element it controls (aria-controls) is {state}.',
                       "Keep aria-expanded in sync with the actual visibility of the controlled content.")]
    return []


def check_disclosure_toggle(page):
    """Accordions / show-hide buttons: Enter or Space must toggle them, and
    aria-expanded must track the panel's visibility."""
    issues = []
    try:
        candidates = page.evaluate(TOGGLE_CANDIDATES_JS, [AUDIT_ATTR, MAX_DISCLOSURES, MAX_MENUS])
    except Exception:
        return []
    for c in candidates:
        if c["kind"] != "disclosure":
            continue
        try:
            if not _focus(page, c["id"]):
                if c["hasExpanded"]:
                    issues.append(_issue(c, ["NAV-1-1"],
                                         "This expand/collapse control cannot receive keyboard focus, so keyboard users cannot open it.",
                                         'Use a native <button>, or add tabindex="0" plus Enter/Space key handling to the custom control.'))
                continue
            key, before, after = _activate(page, c["id"], ["Enter", "Space"])
            if not after.get("exists"):
                continue
            if key is None:
                if c["hasExpanded"]:
                    issues.append(_issue(c, ["NAV-1-1"],
                                         "Pressing Enter or Space on this expand/collapse control changed neither its aria-expanded state nor the visibility of its content.",
                                         "Make the control operable from the keyboard (a native <button> gets Enter/Space for free) and toggle aria-expanded along with the content."))
                continue
            issues.extend(_aria_state_issues(c, before, after, "expand/collapse control"))
            _restore(page, c["id"], key, before)
        except Exception:
            continue
    return issues


def check_menu_keyboard(page):
    """Menus / submenus with aria-expanded or aria-haspopup: the keyboard
    must open them, focus must be able to reach their items, and declared
    menus must close on Escape and return focus to their trigger."""
    issues = []
    try:
        candidates = page.evaluate(TOGGLE_CANDIDATES_JS, [AUDIT_ATTR, MAX_DISCLOSURES, MAX_MENUS])
    except Exception:
        return []
    for c in candidates:
        if c["kind"] != "menu":
            continue
        try:
            if not _focus(page, c["id"]):
                issues.append(_issue(c, ["NAV-1-1"],
                                     "This menu button cannot receive keyboard focus, so keyboard users cannot open its submenu.",
                                     'Use a native <button> (or add tabindex="0" and key handling) for the submenu trigger.'))
                continue
            keys = ["Enter", "ArrowDown"] if c["realLink"] else ["Enter", "Space", "ArrowDown"]
            key, before, after = _activate(page, c["id"], keys)
            if not after.get("exists"):
                continue
            if key is None:
                issues.append(_issue(c, ["NAV-1-1"],
                                     f"The submenu for this menu item did not open with the keyboard ({' / '.join(keys)}); it may only open on mouse hover or click.",
                                     "Make the submenu openable from the keyboard: use a <button aria-expanded> trigger (disclosure pattern) or, for role=\"menu\" widgets, support Enter/Space/Arrow Down."))
                continue
            issues.extend(_aria_state_issues(c, before, after, "menu button"))

            role_menu = c["targetRole"] == "menu"
            if c["hasTarget"] and after.get("shown") and after.get("targetFocusables", 0) > 0 and not after.get("focusInTarget"):
                if role_menu:
                    issues.append(_issue(c, ["NAV-1-1"],
                                         'After opening this role="menu" submenu, keyboard focus stayed on the trigger instead of moving into the menu.',
                                         'When a menu opens, move focus to its first item (and manage focus with arrow keys inside role="menu").'))
                else:
                    page.keyboard.press("Tab")
                    page.wait_for_timeout(150)
                    if not _state(page, c["id"]).get("focusInTarget"):
                        issues.append(_issue(c, ["NAV-1-1"],
                                             "After opening this submenu with the keyboard, pressing Tab did not move focus into it, so its items are skipped or unreachable.",
                                             "Place the submenu directly after its trigger in the DOM (or move focus into it) so the next Tab reaches its first item."))

            if c["declaredMenu"] or role_menu:
                opened = _state(page, c["id"])
                page.keyboard.press("Escape")
                closed = _wait_for_change(page, c["id"], opened)
                if closed.get("exists") and not _changed(opened, closed):
                    issues.append(_issue(c, ["NAV-1-1"],
                                         "Pressing Escape did not close this open submenu.",
                                         "Close the menu on Escape and return focus to the button that opened it."))
                elif closed.get("exists") and not closed.get("focusOnEl"):
                    issues.append(_issue(c, ["NAV-1-1"],
                                         "Escape closed the submenu, but keyboard focus did not return to the button that opened it.",
                                         "After closing the menu with Escape, move focus back to its trigger button."))
            _restore(page, c["id"], key, before)
        except Exception:
            continue
    return issues


HOVER_MENU_CANDIDATES_JS = """
([attr, max]) => {
  const out = [];
  let n = 0;
  for (const li of document.querySelectorAll('nav li, [role="navigation"] li, header li')) {
    if (out.length >= max) break;
    const sub = li.querySelector(':scope > ul, :scope > ol, :scope > div');
    const trig = li.querySelector(':scope > a[href], :scope > button, :scope > span > a[href], :scope > div > a[href]');
    if (!sub || !trig || !sub.querySelector('a[href], button')) continue;
    if (trig.hasAttribute('aria-expanded') || trig.hasAttribute('aria-haspopup')) continue;  // covered by menu_keyboard
    if (li.querySelector(':scope > [aria-expanded], :scope > * > [aria-expanded]')) continue;  // separate toggle button
    if (!__isShown(trig) || __isShown(sub)) continue;
    const id = 'a11y-h' + (n++);
    li.setAttribute(attr, id + '-li');
    trig.setAttribute(attr, id);
    sub.setAttribute(attr, id + '-t');
    out.push({id, selector: __cssPath(trig), html: __snippet(trig)});
  }
  return out;
}
"""


def _move_mouse_away(page):
    vp = page.viewport_size or {"width": 1280, "height": 900}
    page.mouse.move(vp["width"] - 2, vp["height"] - 2)


def check_hover_menu_keyboard(page):
    """CSS/JS submenus with no ARIA that open on mouse hover: the same
    submenu must also open (and be reachable) on keyboard focus."""
    issues = []
    try:
        candidates = page.evaluate(HOVER_MENU_CANDIDATES_JS, [AUDIT_ATTR, MAX_HOVER_MENUS])
    except Exception:
        return []
    for c in candidates:
        try:
            page.locator(f'[{AUDIT_ATTR}="{c["id"]}-li"]').hover(timeout=1500)
            page.wait_for_timeout(350)
            if not _state(page, c["id"]).get("shown"):
                continue  # not a hover menu (click-driven or something else)
            _move_mouse_away(page)
            page.wait_for_timeout(400)
            if _state(page, c["id"]).get("shown"):
                continue  # doesn't close again; can't compare cleanly
            _focus(page, c["id"])
            page.wait_for_timeout(350)
            on_focus = _state(page, c["id"])
            page.keyboard.press("Tab")
            page.wait_for_timeout(200)
            after_tab = _state(page, c["id"])
            if not on_focus.get("shown") and not after_tab.get("focusInTarget"):
                issues.append({
                    "selector": c["selector"], "html": c["html"],
                    "message": "This navigation item's submenu opens on mouse hover but not when the item receives keyboard focus, and there is no button to open it, so keyboard users cannot reach the submenu links.",
                    "fix": 'Show the submenu on :focus-within as well as :hover, or add a <button aria-expanded="false"> next to the link that toggles the submenu.',
                    "test_case_ids": ["NAV-1-2", "NAV-1-1"],
                })
            elif after_tab.get("focusInTarget") and not after_tab.get("shown"):
                issues.append({
                    "selector": c["selector"], "html": c["html"],
                    "message": "Tabbing moves keyboard focus into this submenu's links while the submenu stays hidden, so the focused item is invisible.",
                    "fix": "Show the submenu whenever focus is inside it (e.g. li:focus-within > ul { display: block; }), or remove hidden items from the tab order.",
                    "test_case_ids": ["NAV-1-2", "NAV-1-1"],
                })
            page.evaluate("() => document.activeElement && document.activeElement.blur && document.activeElement.blur()")
        except Exception:
            continue
    return issues


check_disclosure_toggle = _guarded(check_disclosure_toggle)
check_menu_keyboard = _guarded(check_menu_keyboard)
check_hover_menu_keyboard = _guarded(check_hover_menu_keyboard)


# ---------------------------------------------------------------------------
# Registry: name -> callable(page) -> list[issue]
# (interaction-heavy checks are listed last since they take longer)
# ---------------------------------------------------------------------------

CHECKS = [
    ("skip_link", check_skip_link),
    ("access_keys", check_access_keys),
    ("invalid_href", check_invalid_href),
    ("new_tab_links", check_new_tab_links),
    ("target_size", check_target_size),
    ("link_text_consistency", check_link_text_consistency),
    ("generic_link_text", check_generic_link_text),
    ("title_presence", check_title_presence),
    ("heading_structure", check_heading_structure),
    ("paragraph_markup", check_paragraph_markup),
    ("list_grouping", check_list_grouping),
    ("semantic_markup", check_semantic_markup),
    ("layout_tables", check_layout_tables),
    ("table_captions", check_table_captions),
    ("modal_static_aria", check_modal_static_aria),
    ("blink_flash", check_blink_flash),
    ("media_controls", check_media_controls),
    ("media_tracks", check_media_tracks),
    ("tab_order", check_tab_order),
    ("keyboard_reachability", check_keyboard_reachability),
    ("inline_event_handlers", check_inline_event_handlers),
    ("describedby_wiring", check_describedby_wiring),
    ("live_region_presence", check_live_region_presence),
    ("visually_hidden_technique", check_visually_hidden_technique),
    ("fieldset_grouping", check_fieldset_grouping),
    ("required_fields", check_required_fields),
    ("autocomplete_purpose", check_autocomplete_purpose),
    ("auth_field_methods", check_auth_field_methods),
    ("form_structure", check_form_structure),
    ("role_semantics", check_role_semantics),
    ("pagination", check_pagination),
    ("find_content_methods", check_find_content_methods),
    ("wizard_context", check_wizard_context),
    ("reading_order", check_reading_order),
    ("parked_content", check_parked_content),
    ("hover_focus_parity", check_hover_focus_parity),
    ("disabled_focus", check_disabled_focus_static),
    ("reflow", check_reflow),
    ("orientation", check_orientation),
    ("zoom_resize", check_zoom_resize),
    ("text_spacing", check_text_spacing),
    ("focus_visibility", check_focus_visibility),
    ("keyboard_trap", check_keyboard_trap),
    ("media_embeds", check_media_embeds),
    ("disclosure_toggle", check_disclosure_toggle),
    ("menu_keyboard", check_menu_keyboard),
    ("hover_menu_keyboard", check_hover_menu_keyboard),
]


def run_all_checks(page):
    """Runs every custom check against the page. Returns a flat list of
    issues, each tagged with its originating check name. A failure in one
    check never aborts the others."""
    try:
        page.evaluate(PRELUDE_JS)
    except Exception:
        pass
    all_issues = []
    for name, fn in CHECKS:
        try:
            found = fn(page) or []
        except Exception:
            found = []
        for issue in found:
            issue = dict(issue)
            issue["check_name"] = name
            all_issues.append(issue)
    return all_issues


def collect_page_meta(page):
    """Cross-page metadata used by batch-level checks (report.py)."""
    try:
        page.evaluate(PRELUDE_JS)
        return page.evaluate("""
        () => {
          const title = document.title || '';
          const h1_count = document.querySelectorAll('h1').length;
          const nav = document.querySelector('nav, [role="navigation"]');
          const has_nav = !!nav;
          const has_current_nav_indicator = !!document.querySelector('[aria-current="page"], nav [class*="active" i], nav [class*="current" i]');
          const navLinks = nav ? Array.from(nav.querySelectorAll('a[href]')) : [];
          const header_nav_hrefs = navLinks.map(a => a.getAttribute('href'));
          const link_names = navLinks.map(a => [a.getAttribute('href'), (a.getAttribute('aria-label') || a.textContent || '').trim().replace(/\\s+/g, ' ')]);
          return {title, h1_count, has_nav, has_current_nav_indicator, header_nav_hrefs, link_names};
        }
        """)
    except Exception:
        return {"title": "", "h1_count": 0, "has_nav": False, "has_current_nav_indicator": False, "header_nav_hrefs": [], "link_names": []}
