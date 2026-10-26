// @author lxy

export type AgentEventName =
  | "message"
  | "tool"
  | "approval_required"
  | "error"
  | "done"
  | (string & {});

export interface AgentEvent<T = unknown> {
  event: AgentEventName;
  data: T;
}

const FRAME_SEPARATOR = /\r\n\r\n|\n\n|\r\r/;

export async function* parseSseStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<AgentEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        buffer += decoder.decode();
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      let separator = FRAME_SEPARATOR.exec(buffer);
      while (separator?.index !== undefined) {
        const frame = buffer.slice(0, separator.index);
        buffer = buffer.slice(separator.index + separator[0].length);
        const event = parseFrame(frame);
        if (event) {
          yield event;
        }
        separator = FRAME_SEPARATOR.exec(buffer);
      }
    }

    const finalEvent = parseFrame(buffer);
    if (finalEvent) {
      yield finalEvent;
    }
  } finally {
    reader.releaseLock();
  }
}

function parseFrame(frame: string): AgentEvent | null {
  let event: AgentEventName = "message";
  const dataLines: string[] = [];

  for (const line of frame.split(/\r\n|\r|\n/)) {
    if (!line || line.startsWith(":")) {
      continue;
    }
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) {
      value = value.slice(1);
    }
    if (field === "event") {
      event = value || "message";
    } else if (field === "data") {
      dataLines.push(value);
    }
  }

  if (dataLines.length === 0) {
    return null;
  }

  const rawData = dataLines.join("\n");
  return { event, data: parseData(rawData) };
}

function parseData(rawData: string): unknown {
  try {
    return JSON.parse(rawData) as unknown;
  } catch {
    return rawData;
  }
}
