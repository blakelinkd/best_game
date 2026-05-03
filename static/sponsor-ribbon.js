(function () {
  'use strict';

  var CONFIG_URL = 'https://raw.githubusercontent.com/biotachyonic/best_game/main/sponsor-config.json';
  var CACHE_KEY = 'sponsor_ribbon_config';
  var CACHE_TTL = 3600000;
  var POLL_INTERVAL = 60000;

  var defaults = {
    label: 'Brought to you by',
    name: 'biotachyonic',
    cta: 'Watch live \u2197',
    title: 'Watch biotachyonic on Twitch',
    url: 'https://www.twitch.tv/biotachyonic'
  };

  function readDefaults() {
    var label = document.getElementById('sponsor-ribbon-label');
    var name = document.querySelector('.sponsor-ribbon__name');
    var cta = document.getElementById('sponsor-ribbon-cta');
    var link = document.getElementById('sponsor-ribbon-link');
    if (label) defaults.label = label.textContent;
    if (name) defaults.name = name.textContent;
    if (cta) defaults.cta = cta.textContent;
    if (link) {
      defaults.title = link.title;
      defaults.url = link.href;
    }
  }

  function apply(config) {
    var label = document.getElementById('sponsor-ribbon-label');
    var name = document.querySelector('.sponsor-ribbon__name');
    var cta = document.getElementById('sponsor-ribbon-cta');
    var link = document.getElementById('sponsor-ribbon-link');

    if (config.label && label) label.textContent = config.label;
    if (config.name && name) name.textContent = config.name;
    if (config.cta && cta) cta.textContent = config.cta;
    if (config.title && link) link.title = config.title;
    if (config.url && link) link.href = config.url;

    readDefaults();
  }

  function fetchConfig() {
    try {
      var cached = localStorage.getItem(CACHE_KEY);
      if (cached) {
        var parsed = JSON.parse(cached);
        if (Date.now() - parsed.ts < CACHE_TTL) {
          apply(parsed.data);
          return;
        }
      }
    } catch (_) {}

    fetch(CONFIG_URL, { cache: 'no-store' })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) {
        if (data) {
          try {
            localStorage.setItem(CACHE_KEY, JSON.stringify({ data: data, ts: Date.now() }));
          } catch (_) {}
          apply(data);
        }
      })
      .catch(function () {});
  }

  function startPoll() {
    var link = document.getElementById('sponsor-ribbon-link');
    var label = document.getElementById('sponsor-ribbon-label');
    var cta = document.getElementById('sponsor-ribbon-cta');
    if (!link || !label || !cta) return;

    var compact = function (n) {
      if (n >= 1000) return (n / 1000).toFixed(n >= 10000 ? 0 : 1).replace(/\.0$/, '') + 'K';
      return String(n);
    };

    function check() {
      fetch('/api/sponsor-live', { cache: 'no-store' })
        .then(function (res) { return res.ok ? res.json() : null; })
        .then(function (data) {
          if (!data) return;
          if (data.live) {
            link.classList.add('is-live');
            label.textContent = 'Live now \u2014';
            var viewers = Number(data.viewer_count || 0);
            var playing = (data.game_name || data.title || '').trim();
            var truncated = playing.length > 36 ? playing.slice(0, 35) + '\u2026' : playing;
            var playingPart = truncated ? ' \u00b7 ' + truncated : '';
            cta.textContent = compact(viewers) + ' watching' + playingPart;
            var tooltipBits = [defaults.name + ' is live'];
            if (data.game_name) tooltipBits.push('Playing: ' + data.game_name);
            if (data.title) tooltipBits.push(data.title);
            link.title = tooltipBits.join(' \u2014 ');
          } else {
            link.classList.remove('is-live');
            label.textContent = defaults.label;
            cta.textContent = defaults.cta;
            link.title = defaults.title;
          }
        })
        .catch(function () {});
    }

    readDefaults();
    check();
    setInterval(check, POLL_INTERVAL);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      fetchConfig();
      startPoll();
    });
  } else {
    fetchConfig();
    startPoll();
  }
})();
