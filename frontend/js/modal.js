// Event detail modal
const modal = document.getElementById("event-modal");
const modalOverlay = document.getElementById("modal-overlay");

function openModal(eventInfo) {
  const props = eventInfo.event.extendedProps;
  const el = document.getElementById("modal-content");

  // Image
  const imageHtml = props.image_url
    ? `<img src="${props.image_url}" alt="${props.name}" class="modal-image">`
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
    if (props.doors_time) timeHtml += `<span>Doors: ${props.doors_time}</span>`;
    if (props.show_time) timeHtml += `<span>Show: ${props.show_time}</span>`;
    timeHtml += "</div>";
  }

  // Price
  const priceHtml = props.price
    ? `<div class="modal-price">${props.price}</div>`
    : "";

  // Genre
  const genreHtml = props.genre
    ? `<div class="modal-genre">${props.genre}${props.subgenre ? " / " + props.subgenre : ""}</div>`
    : "";

  // Age
  const ageHtml = props.age_restriction
    ? `<div class="modal-age">${props.age_restriction}</div>`
    : "";

  // Support
  const supportHtml = props.support_artists
    ? `<div class="modal-support">with ${props.support_artists}</div>`
    : "";

  // Format date
  const eventDate = new Date(props.date + "T12:00:00");
  const dateStr = eventDate.toLocaleDateString("en-US", {
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
  });

  // Ticket button
  const ticketBtn = props.ticket_url
    ? `<a href="${props.ticket_url}" target="_blank" rel="noopener" class="btn-tickets">Get Tickets</a>`
    : "";

  el.innerHTML = `
    ${imageHtml}
    <div class="modal-body">
      <div class="modal-header-row">
        <h2>${props.artist || props.name}</h2>
        ${statusBadge}
      </div>
      ${props.artist && props.artist !== props.name ? `<p class="modal-event-name">${props.name}</p>` : ""}
      ${supportHtml}
      <div class="modal-venue" style="color: ${props.venue_color}">
        ${props.venue_name} &mdash; ${props.venue_city}
      </div>
      <div class="modal-date">${dateStr}</div>
      ${timeHtml}
      ${priceHtml}
      ${genreHtml}
      ${ageHtml}
      ${props.description ? `<p class="modal-description">${props.description}</p>` : ""}
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
