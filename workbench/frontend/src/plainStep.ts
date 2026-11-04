/** A tool call, said the way a person would. Never the raw name or the raw arguments. */

function field(args: string, key: string): string {
  try {
    const value = JSON.parse(args)?.[key];
    return typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "";
  } catch {
    return "";
  }
}

function clip(text: string, n = 72): string {
  const t = text.trim();
  return t.length > n ? `${t.slice(0, n - 1)}…` : t;
}

const SAY: Record<string, (args: string) => string> = {
  web_search: (a) => withSubject("Searching", field(a, "query")),
  read_page: () => "Reading a page",
  browse_web: (a) => withSubject("Looking through a site", field(a, "goal")),
  search_email: (a) => withSubject("Looking through mail", field(a, "query")),
  read_email: () => "Reading a message",
  list_mail_accounts: () => "Checking the mailboxes",
  save_email_draft: () => "Writing a draft",
  send_email: () => "Preparing to send mail",
  list_tasks: () => "Checking tasks",
  create_task: (a) => withSubject("Adding a task", field(a, "title")),
  update_task: () => "Updating a task",
  get_agenda: () => "Checking the day",
  list_events: () => "Checking the calendar",
  create_event: (a) => withSubject("Adding to the calendar", field(a, "title")),
  list_calendars: () => "Checking the calendars",
  find_free_time: () => "Looking for a free time",
  search_notes: (a) => withSubject("Looking through notes", field(a, "query")),
  read_note: () => "Reading a note",
  create_note: () => "Writing a note",
  append_to_note: () => "Adding to a note",
  read_file: () => "Reading a file",
  write_file: () => "Writing a file",
  edit_file: () => "Editing a file",
  run_command: () => "Running something on this computer",
  use_computer: () => "Using this computer",
  remember: () => "Remembering that",
  forget: () => "Forgetting that",
  search_memory: () => "Looking through what it remembers",
  search_history: (a) => withSubject("Looking through past chats", field(a, "query")),
  schedule_task: (a) => withSubject("Setting something to run later", field(a, "name")),
  list_schedules: () => "Checking what runs later",
};

function withSubject(verb: string, subject: string): string {
  return subject ? `${verb}: ${clip(subject)}` : verb;
}

export function plainStep(name: string, args = ""): string {
  const say = SAY[name];
  return say ? say(args) : "Working";
}
