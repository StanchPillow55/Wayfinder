// Local jobs mirror. No application is ever sent anywhere.
const WF_ROLES = {
  assistant: { title: "Library page assistant", place: "Harbor Branch, on site", pay: "$19 per hour", remote: false,
    about: "Shelve books, help people find them, and keep the reading room tidy.", needs: "Reads shelf labels. Comfortable standing for an hour." },
  transcriber: { title: "Braille transcription trainee", place: "Remote", pay: "$22 per hour", remote: true,
    about: "Learn to turn print documents into Braille files with a mentor.", needs: "Careful with detail. Training is provided." },
  helper: { title: "Community tech helper", place: "Remote or Harbor Branch", pay: "$24 per hour", remote: true,
    about: "Help neighbors set up screen readers, captions, and large-text settings.", needs: "Patient teacher. Some experience with phones or laptops." },
};
