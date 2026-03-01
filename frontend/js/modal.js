// Event detail modal

// Escape user-visible text before injecting into innerHTML.
function _h(s) {
  return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

const modal = document.getElementById("event-modal");
const modalOverlay = document.getElementById("modal-overlay");

function _buildEventRow(ev) {
  const p = ev.extendedProps;
  const safeUrl = p.ticket_url && /^https?:\/\//i.test(p.ticket_url) ? p.ticket_url : null;

  let badge = "";
  if (p.status === "sold_out")  badge = '<span class="badge badge-sold-out">Sold Out</span>';
  if (p.status === "cancelled") badge = '<span class="badge badge-cancelled">Cancelled</span>';

  const meta = [
    p.show_time ? `Show: ${_h(p.show_time)}` : null,
    p.price     ? _h(p.price)                 : null,
    p.age_restriction ? _h(p.age_restriction) : null,
  ].filter(Boolean).join(" &bull; ");

  return `
    <div class="modal-group-event">
      <div class="modal-group-event-header">
        <span class="modal-group-event-title">${_h(ev.title)}</span>
        ${badge}
      </div>
      ${p.support_artists ? `<div class="modal-group-support">with ${_h(p.support_artists)}</div>` : ""}
      ${meta ? `<div class="modal-group-meta">${meta}</div>` : ""}
      ${safeUrl ? `<a href="${safeUrl}" target="_blank" rel="noopener" class="btn-tickets btn-tickets-sm">Get Tickets</a>` : ""}
    </div>`;
}

function _openGroupModal(events, dateStr, venueName, venueColor) {
  const el = document.getElementById("modal-content");
  const sorted = [...events].sort((a, b) => {
    const ta = a.extendedProps.show_time || "99:99";
    const tb = b.extendedProps.show_time || "99:99";
    return ta.localeCompare(tb);
  });

  el.innerHTML = `
    <div class="modal-body">
      <div class="modal-venue" style="color: ${_h(venueColor)}">${_h(venueName)}</div>
      <div class="modal-date">${dateStr}</div>
      <div class="modal-group-list">
        ${sorted.map(_buildEventRow).join("")}
      </div>
    </div>`;

  modal.classList.add("active");
  modalOverlay.classList.add("active");
}

function openModal(eventInfo) {
  const props = eventInfo.event.extendedProps;
  const el = document.getElementById("modal-content");

  // Format date (shared by single and group modal paths)
  const eventDate = new Date(props.date + "T12:00:00");
  const dateStr = eventDate.toLocaleDateString("en-US", {
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
  });

  // If this venue uses day-grouping, collect all events for the same venue+date
  // (including hidden ones) and show them all in a list modal.
  if (typeof GROUPED_VENUE_SLUGS !== "undefined" && GROUPED_VENUE_SLUGS.has(props.venue_slug) &&
      typeof calendar !== "undefined") {
    const sameDayEvents = calendar.getEvents().filter(
      (ev) => ev.extendedProps.venue_slug === props.venue_slug &&
               ev.extendedProps.date === props.date
    );
    if (sameDayEvents.length > 1) {
      _openGroupModal(sameDayEvents, dateStr, props.venue_name, props.venue_color);
      return;
    }
  }

  // Image
  const imageHtml = props.image_url
    ? `<img src="${props.image_url}" alt="${_h(props.name)}" class="modal-image">`
    : "";

  // Status badge
  let statusBadge = "";
  if (props.status === "sold_out") {
    statusBadge = '<span class="badge badge-sold-out">Sold Out</span>';
  } else if (props.status === "cancelled") {
    statusBadge = '<span class="badge badge-cancelled">Cancelled</span>';
  } else if (props.status === "free") {
    statusBadge = '<span class="badge badge-free">Free</span>';
  }

  // Times
  let timeHtml = "";
  if (props.doors_time || props.show_time) {
    timeHtml = '<div class="modal-times">';
    if (props.doors_time) timeHtml += `<span>Doors: ${_h(props.doors_time)}</span>`;
    if (props.show_time) timeHtml += `<span>Show: ${_h(props.show_time)}</span>`;
    timeHtml += "</div>";
  }

  // Price
  const priceHtml = props.price
    ? `<div class="modal-price">${_h(props.price)}</div>`
    : "";

  // Genre
  const genreHtml = props.genre
    ? `<div class="modal-genre">${_h(props.genre)}${props.subgenre ? " / " + _h(props.subgenre) : ""}</div>`
    : "";

  // Age
  const ageHtml = props.age_restriction
    ? `<div class="modal-age">${_h(props.age_restriction)}</div>`
    : "";

  // Support
  const supportHtml = props.support_artists
    ? `<div class="modal-support">with ${_h(props.support_artists)}</div>`
    : "";

  // Ticket button — only allow http/https URLs
  const safeUrl = props.ticket_url && /^https?:\/\//i.test(props.ticket_url) ? props.ticket_url : null;
  const ticketBtn = safeUrl
    ? `<a href="${safeUrl}" target="_blank" rel="noopener" class="btn-tickets">Get Tickets</a>`
    : "";

  el.innerHTML = `
    ${imageHtml}
    <div class="modal-body">
      <div class="modal-header-row">
        <h2>${_h(props.artist || props.name)}</h2>
        ${statusBadge}
      </div>
      ${props.artist && props.artist !== props.name ? `<p class="modal-event-name">${_h(props.name)}</p>` : ""}
      ${supportHtml}
      <div class="modal-venue" style="color: ${_h(props.venue_color)}">
        ${_h(props.venue_name)} &mdash; ${_h(props.venue_city)}
      </div>
      <div class="modal-date">${dateStr}</div>
      ${timeHtml}
      ${priceHtml}
      ${genreHtml}
      ${ageHtml}
      ${props.description ? `<p class="modal-description">${_h(props.description)}</p>` : ""}
      ${ticketBtn}
    </div>
  `;

  modal.classList.add("active");
  modalOverlay.classList.add("active");
}

function closeModal() {
  modal.classList.remove("active");
  modalOverlay.classList.remove("active");
}

// Close on overlay click or Escape key
modalOverlay.addEventListener("click", closeModal);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
});
