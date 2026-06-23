import { createClient } from '@supabase/supabase-js';

export default async (req) => {
  if (req.method !== 'POST') {
    return new Response('Method not allowed', { status: 405 });
  }

  const body = await req.json();
  const { email, mode, settings } = body;

  if (!email || !mode) {
    return new Response(JSON.stringify({ error: 'Missing email or mode' }), { status: 400 });
  }

  const supabase = createClient(
    process.env.SUPABASE_URL,
    process.env.SUPABASE_ANON_KEY
  );

  // Generate a simple unsubscribe token
  const unsubToken = crypto.randomUUID();

  const { error } = await supabase
  .from('subscriptions')
  .upsert({
    email: email.toLowerCase().trim(),
    mode,
    settings: settings || {},
    active: true,
    unsub_token: unsubToken,
    last_sent: null
  }, { 
    onConflict: 'email' 
  });

  if (error) {
    console.error('Supabase error:', error);
    return new Response(JSON.stringify({ error: 'Could not save subscription' }), { status: 500 });
  }

  return new Response(JSON.stringify({ ok: true, message: 'Subscribed successfully' }), { status: 200 });
};
