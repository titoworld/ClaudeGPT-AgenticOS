// The preview of an attachment in the app (components/AttachmentViewer.svelte): one at
// a time, opened from its card in the composer or in a question.

import type { Attachment } from './protocol';

class AttachmentViewer {
  current: Attachment | null = $state.raw(null);

  open(attachment: Attachment): void {
    this.current = attachment;
  }

  close(): void {
    this.current = null;
  }
}

export const viewer = new AttachmentViewer();
