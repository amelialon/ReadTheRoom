-- Fictional demo data. Safe to run again: existing ids are left as they are.
-- Bed 4 matches the bedside app's default Config.bed.

insert into public.patients (id, display_name, bed)
values ('11111111-1111-4111-8111-111111111111', 'Demo Patient', 'Bed 4')
on conflict (id) do nothing;

insert into public.devices (id, patient_id, name)
values (
  '22222222-2222-4222-8222-222222222222',
  '11111111-1111-4111-8111-111111111111',
  'bedside-demo'
)
on conflict (id) do nothing;
