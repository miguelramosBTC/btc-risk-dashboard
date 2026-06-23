import { createClient } from '@supabase/supabase-js';
import { getLatestRisk } from '../../btc-risk-weekly.mjs';

export const config = {
  schedule: '0 8 * * *'   // Every day at 08:00 UTC
};

export default async () => {
  const supabase = createClient(
    process.env.SUPABASE_URL,
    process.env.SUPABASE_ANON_KEY
  );

  // 1. Get current risk
  const reading = await getLatestRisk();
  console.log('Current risk:', reading.risk);

  // 2. Get all active subscriptions
  const { data: subs, error } = await supabase
    .from('subscriptions')
    .select('*')
    .eq('active', true);

  if (error) {
    console.error('Error fetching subscriptions:', error);
    return;
  }

  console.log(`Found ${subs.length} active subscriptions`);

  for (const sub of subs) {
    const shouldSend = shouldSendNotification(sub, reading);

    if (shouldSend) {
      // TODO: Build nice email and send via Resend
      console.log(`Would send to ${sub.email} (mode: ${sub.mode})`);
      
      // For now we just mark it as sent
      await supabase
        .from('subscriptions')
        .update({ last_sent: new Date().toISOString() })
        .eq('id', sub.id);
    }
  }

  return new Response('Done');
};

function shouldSendNotification(sub, reading) {
  // Very basic version for now — we will expand this
  if (sub.mode === 'time') {
    // Example: only send on Mondays for weekly
    const today = new Date().getDay(); // 0=Sunday, 1=Monday...
    if (sub.settings?.freq === 'weekly' && today === 1) {
      return true;
    }
  }
  return false;
}