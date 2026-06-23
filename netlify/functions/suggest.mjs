import { createClient } from '@supabase/supabase-js';

export default async (req) => {
  if (req.method !== 'POST') {
    return new Response('Method not allowed', { status: 405 });
  }

  const body = await req.json();
  const { email, message } = body;

  if (!message || message.trim().length < 12) {
    return new Response(JSON.stringify({ error: 'Message too short' }), { status: 400 });
  }

  const supabase = createClient(
    process.env.SUPABASE_URL,
    process.env.SUPABASE_ANON_KEY
  );

  const { error } = await supabase
    .from('Suggestions')
    .insert({
      email: email || null,
      message: message.trim()
    });

  if (error) {
    console.error('Supabase error:', error);
    return new Response(JSON.stringify({ error: 'Could not save suggestion' }), { status: 500 });
  }

  return new Response(JSON.stringify({ ok: true }), { status: 200 });
};
