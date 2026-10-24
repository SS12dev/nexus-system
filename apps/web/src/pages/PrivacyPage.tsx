import { Link } from "react-router";
import { useAssistantName } from "../lib/assistant";

/** What Nexus keeps, who can see it, and how to take it with you or delete it. Linked from
 * onboarding and Settings. Kept plain on purpose: friends should be able to read it in a minute. */
export function PrivacyPage() {
  const assistant = useAssistantName();
  return (
    <div className="flex-1 overflow-y-auto">
      <article className="prose prose-invert prose-sm mx-auto max-w-2xl px-4 py-6">
        <h1>Your data and privacy</h1>
        <p>
          This Nexus is run privately by its owner for a few people. Here is what it keeps and who
          can see it.
        </p>
        <h2>What's stored</h2>
        <ul>
          <li>Your chats, files you upload or {assistant} makes, and your routines.</li>
          <li>
            What {assistant} remembers about you: your setup answers and what it learns in chats.
            All of it is on the <Link to="/memory">Memory</Link> page, where you can edit or delete
            any of it, or turn learning off.
          </li>
          <li>
            If you connect Google, an access token for the services you allowed. Disconnecting in{" "}
            <Link to="/settings">Settings</Link> revokes it at Google too.
          </li>
        </ul>
        <h2>Who can see it</h2>
        <ul>
          <li>
            Other people on this Nexus, admins included, can't open your chats, files, memories or
            routines in the app.
          </li>
          <li>
            Usage records (which model, how many tokens, the cost) are kept for every message so
            the owner can watch spending. The text of your prompts and replies is hidden in those
            records.
          </li>
          <li>
            To answer you, your messages go to the AI model provider (OpenAI) through the
            owner's account.
          </li>
          <li>
            Like any server, whoever runs the machine could technically reach its database. The
            owner is trusting you with their budget; you're trusting them with this.
          </li>
        </ul>
        <h2>Helping Nexus improve</h2>
        <p>
          Every week Nexus looks at how it was used to suggest improvements to itself: counts
          (tools, failures, costs) and short anonymous topics such as "trip planning", never names
          or chat text, and only topics several people asked about. You can leave your requests out
          of this in <Link to="/settings">Settings</Link>.
        </p>
        <h2>Taking it with you, or leaving</h2>
        <p>
          In <Link to="/settings">Settings</Link> you can download everything as a zip, or delete
          your account, which removes your chats, files, memories, routines and Google connection
          for good.
        </p>
      </article>
    </div>
  );
}
