(function () {
  "use strict";

  // Fallbacks only matter on pages where data.js is absent; data.agent wins when present.
  var DEFAULT_WHATSAPP = "905388919111";
  var DEFAULT_EMAIL = "adildinc@remaxkuvars.com";
  var ALL_FILTER = "Tümü";
  var RENT_OPERATION = "Kiralık";
  var DEFAULT_TYPE_GROUP = "Diğer";
  var FALLBACK_SWAPPED_ATTR = "data-fallback-swapped";
  var MONTHS_PER_YEAR = 12;
  var PERCENT = 100;
  var MAX_REVIEWS = 6;
  var FULL_STARS = "★★★★★";
  var DESKTOP_NAV_QUERY = "(min-width: 1024px)";
  var SMALL_IMAGE_WIDTH = 600;
  var MEDIUM_IMAGE_WIDTH = 1200;
  var IMAGE_HEIGHT_RATIO = 397 / 600;
  var CARD_IMAGE_SIZES = "(min-width: 1180px) 360px, (min-width: 720px) 45vw, 100vw";
  // Only a dot followed by exactly three digits is a Turkish thousands separator, so "2.5" stays 2.5.
  var THOUSANDS_DOT = /\.(?=\d{3}(\D|$))/g;

  var data = window.SITE_DATA || null;
  var agent = (data && data.agent) || {};
  var whatsappNumber = agent.whatsapp || DEFAULT_WHATSAPP;
  var priceFormatter = new Intl.NumberFormat("tr-TR");
  var ratingFormatter = new Intl.NumberFormat("tr-TR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  var yearsFormatter = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 1 });

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function parseLocaleNumber(raw) {
    return parseFloat(String(raw).trim().replace(THOUSANDS_DOT, "").replace(",", "."));
  }

  function whatsappUrl(message) {
    return "https://wa.me/" + whatsappNumber + (message ? "?text=" + encodeURIComponent(message) : "");
  }

  function telHref(phone) {
    return "tel:+" + String(phone).replace(/\D/g, "");
  }

  /* ---------- navigation ---------- */

  function setupNav() {
    var header = document.querySelector(".nav");
    var toggle = header && header.querySelector(".nav-toggle");
    var menu = document.getElementById("site-menu");
    if (!toggle || !menu) return;
    var desktop = window.matchMedia(DESKTOP_NAV_QUERY);

    function setOpen(open, returnFocus) {
      header.classList.toggle("menu-open", open);
      toggle.setAttribute("aria-expanded", String(open));
      if (open) {
        var firstLink = menu.querySelector("a");
        if (firstLink) firstLink.focus();
      } else if (returnFocus) {
        toggle.focus();
      }
    }

    function isOpen() { return toggle.getAttribute("aria-expanded") === "true"; }

    toggle.addEventListener("click", function () { setOpen(!isOpen(), false); });
    menu.addEventListener("click", function (event) {
      if (event.target.closest("a") && isOpen()) setOpen(false, false);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && isOpen()) setOpen(false, true);
    });
    header.addEventListener("focusout", function (event) {
      if (isOpen() && event.relatedTarget && !header.contains(event.relatedTarget)) setOpen(false, false);
    });
    document.addEventListener("click", function (event) {
      if (isOpen() && !header.contains(event.target)) setOpen(false, false);
    });
    desktop.addEventListener("change", function (event) {
      if (event.matches && isOpen()) setOpen(false, false);
    });
  }

  /* ---------- agent-driven values ---------- */

  function applyAgentData() {
    if (agent.whatsapp) {
      document.querySelectorAll('[data-agent-link="whatsapp"]').forEach(function (link) {
        link.href = whatsappUrl("");
      });
    }
    if (agent.phone) {
      document.querySelectorAll('[data-agent-link="phone"]').forEach(function (link) {
        link.href = telHref(agent.phone);
      });
    }
    setText("stat-years", agent.activeYears ? agent.activeYears + " yıl" : null);
    setText("stat-listings", data && data.listings ? String(data.listings.length) : null);
    if (agent.reviewAverage && agent.reviewCount) {
      var average = ratingFormatter.format(agent.reviewAverage);
      setText("stat-rating", average);
      setText("stat-reviews", agent.reviewCount + " müşteri yorumu");
      setText("reviews-summary", "RE/MAX Türkiye üzerinden bırakılan " + agent.reviewCount + " değerlendirmenin ortalaması " + average + ".");
    }
  }

  function setText(id, text) {
    var el = document.getElementById(id);
    if (el && text) el.textContent = text;
  }

  /* ---------- listings ---------- */

  function typeGroupOf(listing) {
    return listing.typeGroup || DEFAULT_TYPE_GROUP;
  }

  function unique(values) {
    return values.filter(function (value, index) { return values.indexOf(value) === index; });
  }

  function formatPrice(listing) {
    if (!listing.price) return '<span class="price">Fiyat için arayın</span>';
    var suffix = listing.operation === RENT_OPERATION ? " <small>/ ay</small>" : "";
    var old = listing.oldPrice ? "<s>" + priceFormatter.format(listing.oldPrice) + " " + escapeHtml(listing.currency) + "</s>" : "";
    return '<span class="price">' + old + priceFormatter.format(listing.price) + " " + escapeHtml(listing.currency) + suffix + "</span>";
  }

  // data.js is generated from an external source, so only http(s) or relative URLs become links.
  function safeHref(url) {
    return /^(https?:\/\/|[^:]*$)/i.test(String(url || "")) ? url : "#";
  }

  function listingLink(listing) {
    if (listing.pageUrl) return { href: safeHref(listing.pageUrl), external: false };
    return { href: safeHref(listing.url), external: true };
  }

  function imageMarkup(listing, alt) {
    var src = listing.imageMedium || listing.image;
    if (!src) return "";
    var srcset = listing.imageSmall && listing.imageMedium
      ? ' srcset="' + escapeHtml(listing.imageSmall) + " " + SMALL_IMAGE_WIDTH + "w, " + escapeHtml(listing.imageMedium) + " " + MEDIUM_IMAGE_WIDTH + 'w" sizes="' + CARD_IMAGE_SIZES + '"'
      : "";
    var fallback = listing.image ? ' data-fallback="' + escapeHtml(listing.image) + '"' : "";
    return '<img loading="lazy" decoding="async" src="' + escapeHtml(src) + '"' + srcset + fallback +
      ' width="' + SMALL_IMAGE_WIDTH + '" height="' + Math.round(SMALL_IMAGE_WIDTH * IMAGE_HEIGHT_RATIO) + '" alt="' + alt + '">';
  }

  function cardMarkup(listing) {
    var title = escapeHtml(listing.displayTitle || listing.title);
    var link = listingLink(listing);
    var target = link.external ? ' target="_blank" rel="noopener"' : "";
    var href = escapeHtml(link.href);
    var rooms = listing.rooms ? "<span>" + escapeHtml(listing.rooms) + "</span>" : "";
    var photos = listing.photoCount ? "<span>" + escapeHtml(listing.photoCount) + " fotoğraf</span>" : "";
    var area = listing.area ? "<span>" + escapeHtml(listing.area) + " m²</span>" : "";
    var place = [listing.town, listing.city].filter(Boolean).map(escapeHtml).join(", ");
    // Same absolute link as the listing page's own WhatsApp button, so both entry points send identical messages.
    var shareUrl = listing.pageUrl ? new URL(listing.pageUrl, window.location.href).href : listing.url;
    var waMessage = "Merhaba Adil Bey, " + listing.code + " numaralı ilanınız hakkında bilgi almak istiyorum: " + shareUrl;
    return '<article class="card">' +
      // The image link duplicates the title link, so it stays out of the tab order and accessibility tree.
      '<a class="card-media" href="' + href + '"' + target + ' tabindex="-1" aria-hidden="true">' + imageMarkup(listing, "") + "</a>" +
      '<div class="card-body">' +
        '<span class="card-op' + (listing.operation === RENT_OPERATION ? " rent" : "") + '">' + escapeHtml(listing.operation) + " " + escapeHtml(listing.category) + "</span>" +
        '<h3><a href="' + href + '"' + target + ">" + title + (link.external ? '<span class="visually-hidden"> (RE/MAX sitesinde, yeni sekmede açılır)</span>' : "") + "</a></h3>" +
        '<div class="card-loc">' + place + "</div>" +
        '<div class="card-meta">' + area + rooms + photos + "</div>" +
        '<div class="card-foot">' + formatPrice(listing) +
          '<a class="wa-mini" href="' + escapeHtml(whatsappUrl(waMessage)) + '" target="_blank" rel="noopener" aria-label="' + escapeHtml(listing.code) + ' numaralı ilan için WhatsApp\'tan yaz"><svg aria-hidden="true"><use href="#i-wa"/></svg></a>' +
        "</div>" +
      "</div>" +
    "</article>";
  }

  function compareByPrice(direction) {
    return function (a, b) {
      // Listings without a price always sink to the end, whichever direction is chosen.
      if (!a.price && !b.price) return a.index - b.index;
      if (!a.price) return 1;
      if (!b.price) return -1;
      return direction * (a.price - b.price) || a.index - b.index;
    };
  }

  var SORTERS = {
    "recommended": function (a, b) { return a.index - b.index; },
    "price-asc": compareByPrice(1),
    "price-desc": compareByPrice(-1)
  };

  function setupListings() {
    var grid = document.getElementById("listing-grid");
    if (!grid || !data || !data.listings) return;
    var opContainer = document.getElementById("op-filters");
    var typeContainer = document.getElementById("type-filters");
    var sortSelect = document.getElementById("listing-sort");
    var status = document.getElementById("listing-status");
    var listings = data.listings.map(function (listing, index) {
      return { listing: listing, index: index, price: listing.price || null, op: listing.operation, type: typeGroupOf(listing) };
    });
    var state = { op: ALL_FILTER, type: ALL_FILTER, sort: "recommended" };

    function matches(item, key, value) {
      return value === ALL_FILTER || item[key] === value;
    }

    function renderChips(container, key, otherKey) {
      if (!container) return;
      var options = [ALL_FILTER].concat(unique(listings.map(function (item) { return item[key]; })));
      // Counts respect the other active filter, so a chip tells you what clicking it will show.
      container.innerHTML = options.map(function (option) {
        var count = listings.filter(function (item) {
          return matches(item, key, option) && matches(item, otherKey, state[otherKey]);
        }).length;
        return '<button type="button" class="chip" data-value="' + escapeHtml(option) + '" aria-pressed="' + (state[key] === option) + '">' +
          escapeHtml(option) + ' <span class="chip-count">' + count + "</span></button>";
      }).join("");
    }

    function render() {
      renderChips(opContainer, "op", "type");
      renderChips(typeContainer, "type", "op");
      var visible = listings.filter(function (item) {
        return matches(item, "op", state.op) && matches(item, "type", state.type);
      }).sort(SORTERS[state.sort] || SORTERS.recommended);
      grid.innerHTML = visible.length
        ? visible.map(function (item) { return cardMarkup(item.listing); }).join("")
        : '<div class="empty">Bu filtrede ilan bulunmuyor.</div>';
      if (status) status.textContent = visible.length + " ilan gösteriliyor";
    }

    function bindChips(container, key) {
      if (!container) return;
      container.addEventListener("click", function (event) {
        var button = event.target.closest("button[data-value]");
        if (!button) return;
        state[key] = button.getAttribute("data-value");
        render();
        var pressed = container.querySelector('button[data-value="' + CSS.escape(state[key]) + '"]');
        if (pressed) pressed.focus();
      });
    }

    bindChips(opContainer, "op");
    bindChips(typeContainer, "type");
    if (sortSelect) {
      sortSelect.addEventListener("change", function () {
        state.sort = sortSelect.value;
        render();
      });
    }
    render();

    var syncNote = document.getElementById("sync-note");
    if (syncNote && data.syncedAt) {
      syncNote.textContent = "İlanlar RE/MAX Türkiye'den otomatik alınır. Son ilan değişikliği: " +
        new Date(data.syncedAt).toLocaleString("tr-TR", { dateStyle: "long", timeStyle: "short" });
    }
  }

  /* ---------- reviews ---------- */

  function setupReviews() {
    var grid = document.getElementById("review-grid");
    if (!grid || !data || !data.reviews) return;
    grid.innerHTML = data.reviews.slice(0, MAX_REVIEWS).map(function (review) {
      var points = Math.max(0, Math.min(FULL_STARS.length, Number(review.point) || 0));
      var date = new Date(review.date).toLocaleDateString("tr-TR", { year: "numeric", month: "long" });
      return '<article class="review reveal"><span class="stars" role="img" aria-label="5 üzerinden ' + points + ' yıldız">' + FULL_STARS.slice(0, points) + "</span>" +
        "<p>" + escapeHtml(review.comment) + "</p>" +
        "<footer><b>" + escapeHtml(review.name) + "</b><span>" + escapeHtml(date) + "</span></footer></article>";
    }).join("");
  }

  /* ---------- loan calculator ---------- */

  function setupCalculator() {
    var form = document.getElementById("calc-form");
    if (!form) return;
    var amountInput = document.getElementById("calc-amount");
    var termInput = document.getElementById("calc-term");
    var rateInput = document.getElementById("calc-rate");
    var paymentEl = document.getElementById("calc-payment");
    var totalEl = document.getElementById("calc-total");

    function update() {
      var principal = parseLocaleNumber(amountInput.value);
      var months = parseLocaleNumber(termInput.value);
      var monthlyRate = parseLocaleNumber(rateInput.value) / PERCENT;
      if (!(principal > 0) || !(months > 0) || !(monthlyRate >= 0)) {
        paymentEl.textContent = "–";
        totalEl.textContent = "Geçerli tutar, vade ve oran girin.";
        return;
      }
      var payment = monthlyRate === 0
        ? principal / months
        : principal * monthlyRate / (1 - Math.pow(1 + monthlyRate, -months));
      paymentEl.textContent = priceFormatter.format(Math.round(payment)) + " ₺";
      totalEl.textContent = "Toplam geri ödeme: " + priceFormatter.format(Math.round(payment * months)) + " ₺ (" +
        yearsFormatter.format(months / MONTHS_PER_YEAR) + " yıl)";
    }

    form.addEventListener("submit", function (event) { event.preventDefault(); });
    form.addEventListener("input", update);
    update();
  }

  /* ---------- valuation form ---------- */

  function setupValuationForm() {
    var form = document.getElementById("valuation-form");
    if (!form) return;
    var mailLink = document.getElementById("valuation-mail");
    var email = agent.email || DEFAULT_EMAIL;

    function summary() {
      var fields = form.elements;
      var lines = [
        "Merhaba Adil Bey, mülkümün değerlendirilmesini istiyorum.",
        "Emlak tipi: " + (fields.propertyType.value || "-"),
        "İşlem: " + fields.operation.value,
        "İl / ilçe: " + (fields.location.value.trim() || "-")
      ];
      if (fields.area.value) lines.push("Alan: " + fields.area.value + " m²");
      if (fields.note.value.trim()) lines.push("Not: " + fields.note.value.trim());
      return lines.join("\n");
    }

    function updateMailLink() {
      if (!mailLink) return;
      mailLink.href = "mailto:" + email + "?subject=" + encodeURIComponent("Mülk değerleme talebi") + "&body=" + encodeURIComponent(summary());
    }

    form.addEventListener("input", updateMailLink);
    form.addEventListener("change", updateMailLink);
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      window.open(whatsappUrl(summary()), "_blank", "noopener");
    });
    updateMailLink();
  }

  /* ---------- reveal on scroll ---------- */

  function setupReveal() {
    if (!("IntersectionObserver" in window) || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) { entry.target.classList.add("in"); observer.unobserve(entry.target); }
      });
    }, { rootMargin: "0px 0px -10% 0px" });
    var viewportHeight = window.innerHeight;
    document.querySelectorAll(".reveal").forEach(function (el) {
      // Anything already on screen stays visible; hiding it now would cause a flash.
      if (el.getBoundingClientRect().top < viewportHeight) return;
      el.classList.add("reveal-pending");
      observer.observe(el);
    });
  }

  /* ---------- gallery lightbox (listing pages) ---------- */

  function setupLightbox() {
    var triggers = Array.prototype.slice.call(document.querySelectorAll(".gallery a[data-full]"));
    if (!triggers.length || typeof HTMLDialogElement !== "function") return;
    var urls = unique(triggers.map(function (link) { return link.getAttribute("data-full"); }));
    var fallbacks = {};
    triggers.forEach(function (link) {
      var thumb = link.querySelector("img[data-fallback]");
      var full = link.getAttribute("data-full");
      if (thumb && !fallbacks[full]) fallbacks[full] = thumb.getAttribute("data-fallback");
    });
    var current = 0;
    var returnFocusTo = null;

    var dialog = document.createElement("dialog");
    dialog.className = "lightbox";
    dialog.setAttribute("aria-label", "Fotoğraf görüntüleyici");
    dialog.innerHTML =
      '<div class="lightbox-stage"><img alt=""></div>' +
      '<button type="button" class="lightbox-close" aria-label="Kapat">×</button>' +
      '<button type="button" class="lightbox-prev" aria-label="Önceki fotoğraf">‹</button>' +
      '<button type="button" class="lightbox-next" aria-label="Sonraki fotoğraf">›</button>';
    document.body.appendChild(dialog);
    var image = dialog.querySelector("img");
    var prevButton = dialog.querySelector(".lightbox-prev");
    var nextButton = dialog.querySelector(".lightbox-next");
    var hasMultiple = urls.length > 1;
    prevButton.hidden = !hasMultiple;
    nextButton.hidden = !hasMultiple;

    function show(index) {
      current = (index + urls.length) % urls.length;
      // Each slide gets a fresh one-shot fallback to the remote original via setupImageFallback.
      image.removeAttribute(FALLBACK_SWAPPED_ATTR);
      if (fallbacks[urls[current]]) image.setAttribute("data-fallback", fallbacks[urls[current]]);
      else image.removeAttribute("data-fallback");
      image.src = urls[current];
      image.alt = "Fotoğraf " + (current + 1) + " / " + urls.length;
    }

    triggers.forEach(function (link) {
      link.addEventListener("click", function (event) {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
        event.preventDefault();
        returnFocusTo = link;
        show(urls.indexOf(link.getAttribute("data-full")));
        dialog.showModal();
      });
    });
    dialog.querySelector(".lightbox-close").addEventListener("click", function () { dialog.close(); });
    prevButton.addEventListener("click", function () { show(current - 1); });
    nextButton.addEventListener("click", function () { show(current + 1); });
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog || event.target.classList.contains("lightbox-stage")) dialog.close();
    });
    dialog.addEventListener("keydown", function (event) {
      if (!hasMultiple) return;
      if (event.key === "ArrowLeft") { event.preventDefault(); show(current - 1); }
      if (event.key === "ArrowRight") { event.preventDefault(); show(current + 1); }
    });
    dialog.addEventListener("close", function () {
      if (returnFocusTo) returnFocusTo.focus();
    });
  }

  /* ---------- image fallback ---------- */

  // Local mirrors can be missing; swap once to the remote original so a failing fallback cannot loop.
  function swapToFallback(img) {
    if (!img.hasAttribute("data-fallback") || img.hasAttribute(FALLBACK_SWAPPED_ATTR)) return;
    img.setAttribute(FALLBACK_SWAPPED_ATTR, "");
    img.removeAttribute("srcset");
    img.removeAttribute("sizes");
    img.src = img.getAttribute("data-fallback");
  }

  function setupImageFallback() {
    document.addEventListener("error", function (event) {
      if (event.target instanceof HTMLImageElement) swapToFallback(event.target);
    }, true);
    // The script is deferred, so images that already failed before it ran are caught here.
    document.querySelectorAll("img[data-fallback]").forEach(function (img) {
      if (img.complete && img.naturalWidth === 0 && img.getAttribute("src")) swapToFallback(img);
    });
  }

  function setYear() {
    var year = document.getElementById("year");
    if (year) year.textContent = new Date().getFullYear();
  }

  setupImageFallback();
  setYear();
  setupNav();
  applyAgentData();
  setupListings();
  setupReviews();
  setupCalculator();
  setupValuationForm();
  setupLightbox();
  setupReveal();
})();
